"""What one committee run cost, estimated from Anthropic's published list prices.

This module is the ONE definition of run cost. Three consumers need the same
number — the Telegram completion message, the persisted evaluation record and
the HTML report — and three implementations of "multiply tokens by a rate"
would drift within a month. Everything here operates on the *serialised* agent
result shape (`Orchestrator._ser` output, which is also what the API returns in
`agent_results` and what `agent_outputs` rows hold), because that is the one
shape all three consumers already have in hand.

TWO CONSTRAINTS ON THIS FILE, BOTH LOAD-BEARING
-----------------------------------------------

1. **Standard library only, and no intra-package imports.** `telegram_bot.py`
   loads this file directly by path (see `_load_pricing` there) rather than
   importing `app.llm.pricing`, because importing the package would execute
   `app/llm/__init__.py` -> `app/utils/types.py`, which uses `TypeAliasType`
   (Python 3.12+). The bot runs under the VPS system interpreter, which is
   3.10.12. Adding `from app.utils.types import ...` here would therefore not
   fail a test — it would silently remove the cost line from every Telegram
   message on the one machine that matters.

2. **Must parse and run on Python 3.10.** Same reason. `from __future__ import
   annotations` keeps the `X | Y` annotations legal there; do not use them in a
   runtime position, and no `match`, no PEP 695 generics.

WHY NOT ``tokens_input + tokens_output``
----------------------------------------
Prompt caching is live (merged 2026-08-25). Since then the API reports
``input_tokens`` as the *uncached remainder only*: the Dolphin run of
2026-08-27 recorded 126 uncached input tokens across fifteen agents against
roughly 1.16M tokens of real prompt, all of it cache writes and cache reads.
Pricing ``tokens_input + tokens_output`` on that run yields $1.69 against a
true $3.59 — a plausible-looking figure that is 47% of the answer. The three
prompt streams are priced separately below, at their own rates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# PUBLISHED PRICE LIST — this is a copy of someone else's price list.
#
#   Source: Anthropic API pricing reference, "Current Models" table
#           https://docs.claude.com/en/docs/about-claude/pricing
#   Taken:  2026-08-27
#
# These are LIST prices in USD per million tokens, not amounts billed. They
# change without any signal reaching this repository, so they carry the date
# they were read and are re-checked, not trusted. Everything computed from them
# is an estimate and is labelled as one everywhere it is shown.
#
# Add a model by adding a row. Do NOT add a row you have not read off the
# pricing page — a wrong rate here is invisible and permanent, whereas a
# missing row is loud (see `RunCost.unpriced_agents`).
# ---------------------------------------------------------------------------

PRICES_TAKEN_ON = "2026-08-27"
PRICES_SOURCE = "https://docs.claude.com/en/docs/about-claude/pricing"

#: model id -> (input $/MTok, output $/MTok)
USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

#: Cache writes are billed at a premium over the input rate, cache reads at a
#: steep discount. Same source and date as the table above.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

_MTOK = 1_000_000.0

#: `config.py` sets `haiku_model = "claude-haiku-4-5-20251001"`, so the id that
#: reaches `AgentResult.model_used` may carry a dated snapshot suffix while the
#: price list is keyed on the base id. Strip exactly a trailing `-YYYYMMDD` and
#: nothing else: a genuinely unknown model must stay unknown rather than be
#: mangled into a known one.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalise_model(model_used: str) -> str:
    """Map a recorded model id onto a key in `USD_PER_MTOK`, if one exists."""
    model = (model_used or "").strip()
    if model in USD_PER_MTOK:
        return model
    stripped = _DATE_SUFFIX.sub("", model)
    return stripped if stripped in USD_PER_MTOK else model


@dataclass(frozen=True)
class AgentCost:
    """One agent's share of the bill, with the four streams kept apart."""

    agent_name: str
    model_used: str
    priced: bool
    #: Set when `priced` is False and the agent actually burned tokens.
    unpriced_reason: str = ""

    tokens_uncached_input: int = 0
    tokens_cache_write: int = 0
    tokens_cache_read: int = 0
    tokens_output: int = 0

    usd_uncached_input: float = 0.0
    usd_cache_write: float = 0.0
    usd_cache_read: float = 0.0
    usd_output: float = 0.0

    #: What the same tokens would have cost with no prompt cache at all: every
    #: prompt token billed once at the full input rate. The difference against
    #: `usd_total` is what caching bought on this agent.
    usd_without_cache: float = 0.0

    @property
    def usd_total(self) -> float:
        return (
            self.usd_uncached_input
            + self.usd_cache_write
            + self.usd_cache_read
            + self.usd_output
        )

    @property
    def tokens_prompt_total(self) -> int:
        """The real prompt size — the thing `tokens_input` alone no longer is."""
        return (
            self.tokens_uncached_input + self.tokens_cache_write + self.tokens_cache_read
        )


@dataclass(frozen=True)
class RunCost:
    """The whole run. `total_usd` is a floor whenever `unpriced_agents` is non-empty."""

    total_usd: float = 0.0
    without_cache_usd: float = 0.0
    agents: list[AgentCost] = field(default_factory=list)
    unpriced_agents: list[AgentCost] = field(default_factory=list)
    unknown_models: list[str] = field(default_factory=list)
    #: False when no agent record carried the cache keys at all. That means the
    #: record predates `_ser` propagating them, not that the run did no
    #: caching — the total is then a floor of unknown tightness, not an
    #: estimate. Live runs always have them.
    cache_fields_present: bool = True

    @property
    def agent_count(self) -> int:
        return len(self.agents)

    @property
    def priced_count(self) -> int:
        return len(self.agents) - len(self.unpriced_agents)

    @property
    def cache_saving_usd(self) -> float:
        """Non-negative by construction; a cache-miss-only run saves nothing."""
        return max(self.without_cache_usd - self.total_usd, 0.0)

    @property
    def complete(self) -> bool:
        return not self.unpriced_agents

    @property
    def priceable(self) -> bool:
        """True if anything at all could be priced. False means say nothing."""
        return self.priced_count > 0


def _int(record: object, key: str) -> int:
    """Token counts arrive from JSON, a DB row or a dataclass. Be forgiving."""
    if isinstance(record, dict):
        raw = record.get(key)
    else:
        raw = getattr(record, key, None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    value = int(raw)
    return value if value > 0 else 0


def price_agent(agent_name: str, record: object) -> AgentCost:
    """Price one serialised agent result. Never raises."""
    model_raw = ""
    if isinstance(record, dict):
        model_raw = str(record.get("model_used") or "")
    else:
        model_raw = str(getattr(record, "model_used", "") or "")
    model = normalise_model(model_raw)

    uncached = _int(record, "tokens_input")
    cache_write = _int(record, "cache_write_tokens")
    cache_read = _int(record, "cache_read_tokens")
    output = _int(record, "tokens_output")
    total_tokens = uncached + cache_write + cache_read + output

    rates = USD_PER_MTOK.get(model)
    if rates is None:
        # An agent that burned nothing costs nothing whatever model it claims,
        # and a run is not "partially unpriced" because a crashed agent never
        # got as far as recording one. Only a model id with real tokens behind
        # it is a genuine hole in the total.
        if total_tokens == 0:
            return AgentCost(agent_name=agent_name, model_used=model_raw, priced=True)
        reason = (
            "no model recorded" if not model_raw else "unknown model %s" % model_raw
        )
        return AgentCost(
            agent_name=agent_name,
            model_used=model_raw,
            priced=False,
            unpriced_reason=reason,
            tokens_uncached_input=uncached,
            tokens_cache_write=cache_write,
            tokens_cache_read=cache_read,
            tokens_output=output,
        )

    in_rate, out_rate = rates
    return AgentCost(
        agent_name=agent_name,
        model_used=model_raw,
        priced=True,
        tokens_uncached_input=uncached,
        tokens_cache_write=cache_write,
        tokens_cache_read=cache_read,
        tokens_output=output,
        usd_uncached_input=uncached / _MTOK * in_rate,
        usd_cache_write=cache_write / _MTOK * in_rate * CACHE_WRITE_MULTIPLIER,
        usd_cache_read=cache_read / _MTOK * in_rate * CACHE_READ_MULTIPLIER,
        usd_output=output / _MTOK * out_rate,
        usd_without_cache=(
            (uncached + cache_write + cache_read) / _MTOK * in_rate
            + output / _MTOK * out_rate
        ),
    )


def price_run(agent_results: object) -> RunCost:
    """Price a whole run from its serialised `agent_results`.

    Accepts the `{agent_name: record}` mapping the API returns, or any iterable
    of records carrying `agent_name`. Never raises: a run that cannot be priced
    comes back empty and the caller says nothing, rather than losing a report.
    """
    records: list[tuple[str, object]] = []
    if isinstance(agent_results, dict):
        for key, value in agent_results.items():
            name = str(key)
            if isinstance(value, dict):
                name = str(value.get("agent_name") or key)
            records.append((name, value))
    elif isinstance(agent_results, (list, tuple)):
        for value in agent_results:
            name = ""
            if isinstance(value, dict):
                name = str(value.get("agent_name") or "")
            else:
                name = str(getattr(value, "agent_name", "") or "")
            records.append((name, value))
    else:
        return RunCost()

    cache_seen = False
    for _, value in records:
        if isinstance(value, dict):
            if "cache_read_tokens" in value or "cache_write_tokens" in value:
                cache_seen = True
                break
        elif hasattr(value, "cache_read_tokens") or hasattr(value, "cache_write_tokens"):
            cache_seen = True
            break

    agents = [price_agent(name, value) for name, value in records]
    unpriced = [a for a in agents if not a.priced]
    unknown = sorted({a.model_used or "(none)" for a in unpriced})

    return RunCost(
        total_usd=sum(a.usd_total for a in agents),
        without_cache_usd=sum(a.usd_without_cache for a in agents),
        agents=agents,
        unpriced_agents=unpriced,
        unknown_models=unknown,
        cache_fields_present=cache_seen or not records,
    )


def _usd(amount: float) -> str:
    """Two decimals, but never round a real cost down to `$0.00`."""
    if 0 < amount < 0.005:
        return "<$0.01"
    return "$%.2f" % amount


def format_cost_line(cost: RunCost) -> str:
    """The Telegram line(s). Empty string means: print nothing.

    One line in the normal case. A second line appears only when part of the
    run could not be priced, because a total that is silently short is worse
    than a total that says so — a run reported at $0.00 because a model id was
    not in the table is exactly the failure this module exists to prevent.
    """
    # Lead with a number only if it means something. A run whose priceable
    # share rounds to nothing while agents are missing from the table is not a
    # cheap run — it is an unknown one, and "~$0.00+" reads as the former at a
    # glance however carefully the second line is worded. This is not
    # hypothetical: Plasma (2026-04-12) prices exactly this way, because its
    # eight working agents ran on retired April model ids and the only agents
    # this table can price are the six that crashed before spending anything.
    if not cost.priceable or (cost.total_usd < 0.005 and cost.unpriced_agents):
        if cost.unpriced_agents:
            return "Cost: not available — %d of %d agents could not be priced (%s)" % (
                len(cost.unpriced_agents),
                cost.agent_count,
                ", ".join(cost.unknown_models),
            )
        return ""

    # "~" and "estimate" together: these are published list prices applied to
    # reported token counts, not an amount Anthropic billed.
    headline = "Cost: ~%s%s (list-price estimate" % (
        _usd(cost.total_usd),
        "+" if not cost.complete else "",
    )
    if cost.cache_fields_present:
        if cost.cache_saving_usd >= 0.005:
            headline += ", prompt cache saved ~%s" % _usd(cost.cache_saving_usd)
    else:
        headline += ", prompt cache not recorded"
    headline += ")"

    if cost.complete:
        return headline

    return headline + "\n%d of %d agents unpriced: %s" % (
        len(cost.unpriced_agents),
        cost.agent_count,
        ", ".join(cost.unknown_models),
    )


def format_cost_breakdown(cost: RunCost) -> list[str]:
    """Per-stream arithmetic, for the HTML report and for checking by hand.

    Not used by the Telegram message — a phone notification is not an invoice.
    """
    lines = [
        "Anthropic list prices as published %s (%s)." % (PRICES_TAKEN_ON, PRICES_SOURCE),
        "%-20s %-18s %10s %10s %10s %10s %9s"
        % ("agent", "model", "in", "cache_w", "cache_r", "out", "usd"),
    ]
    for a in sorted(cost.agents, key=lambda x: x.agent_name):
        lines.append(
            "%-20s %-18s %10d %10d %10d %10d %9s"
            % (
                a.agent_name,
                a.model_used or "-",
                a.tokens_uncached_input,
                a.tokens_cache_write,
                a.tokens_cache_read,
                a.tokens_output,
                _usd(a.usd_total) if a.priced else "UNPRICED",
            )
        )
    lines.append(
        "TOTAL %s   without cache %s   saved %s"
        % (
            _usd(cost.total_usd),
            _usd(cost.without_cache_usd),
            _usd(cost.cache_saving_usd),
        )
    )
    return lines
