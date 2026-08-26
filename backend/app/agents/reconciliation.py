"""Data reconciliation - shared case context and numerical consistency checks.

Two checks live here, and they read different halves of an agent's output.

* **Structured fields** — ``_extract_metrics`` walks the JSON and keeps numbers
  whose leaf key names a metric. High confidence, very low yield: measured
  against the live GMX evaluation it found **5 numbers across 15 agents**, four
  of them from one agent, so no cross-agent comparison was ever possible and
  ``reconcile_data`` reported CLEAN for every evaluation ever run.
* **Prose** — the figures are in ``summary``, ``key_findings``, ``risks`` and
  the Report Writer's 24 sections, as sentences. The same GMX evaluation yields
  **67 comparable claims from 12 agents** once the prose is read.

The prose extractor is NOT written here. ``knowledge/consistency.py`` already
has one, with three precision rules earned against real false positives
(adjacency rather than proximity for metric binding, nearest-preceding entity
attribution with a drop on failure, periods as spans compared by containment).
A second extractor with different rules would disagree with the first, which is
the exact failure class both modules exist to catch. This module imports that
one and re-keys its output; see ``_run_claims``.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

#: Leaf names that make a number worth reconciling, matched against the key with
#: separators removed so "market_cap", "marketCap" and "marketcap" all hit.
METRIC_TERMS = ("tvl", "supply", "revenue", "marketcap", "fdv")

#: Two agents must differ by more than this fraction to be flagged.
DIVERGENCE_THRESHOLD = 0.2


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """QA-023: the same explicit-None defect as QA-015, one step earlier.

    build_case_context runs before any agent does, so an AttributeError here
    aborts the whole evaluation rather than degrading to an unknown-metrics
    baseline.
    """
    return value if isinstance(value, Mapping) else {}


#: What an agent is obliged to do with the baseline, rendered to the model as
#: part of the metrics block.
#:
#: WHY THIS STRING LIVES INSIDE ``canonical_metrics``
#:
#: ``BaseAgent.get_system_prompt`` renders exactly two things from the case
#: context — ``canonical_metrics`` and ``case_time``. ``data_snapshot_note`` has
#: never reached a model. ``base.py`` is owned by another branch, so the only
#: channel this module controls that an agent actually reads is the metrics dict
#: itself. The leading underscore keeps it sorted ahead of the figures under
#: ``json.dumps(..., sort_keys=True)`` and marks it as not-a-metric.
#:
#: The obligation is deliberately stronger than the "flag discrepancies" heading
#: in base.py. Flagging is optional and unowned; departing on the record is not.
#: The final sentence is the part aimed squarely at the defect: the GMX report
#: built a decision trigger ("a material loss of its 70-80% dominance would
#: upgrade the call") on a category-share figure that no source had made
#: canonical and that the Hyperliquid report, seventeen minutes earlier, had put
#: at ~44%. A trigger anchored to an undated, unsourced number can never fire
#: correctly.
BASELINE_RULE = (
    "Canonical figures for this evaluation, dated in _as_of. Use them as given. If a "
    "source you find disagrees materially, do not silently substitute it: record the "
    "departure in key_findings — canonical figure, your figure, your source, your "
    "source's own as-of date, and why. Any quantity absent here (market or category "
    "share, peer or sector volumes, rankings) is NOT canonical: quote it only with an "
    "explicit as-of date and a named source, and never anchor a threshold, signpost or "
    "trigger on one that lacks them."
)


def _stamp(moment: datetime) -> str:
    """Second-precision UTC. Microseconds are four tokens of noise per stamp."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trim_stamp(raw: object) -> str:
    text = str(raw or "")
    try:
        return _stamp(datetime.fromisoformat(text))
    except ValueError:
        return text or "unknown"


def _defillama_clause(defi_facts: Mapping[str, Any]) -> str:
    """One clause saying what DeFiLlama gave us, or why it gave us nothing."""
    if not defi_facts:
        return "tvl/fees/revenue: not fetched"
    slug = defi_facts.get("slug")
    unavailable = defi_facts.get("unavailable")
    if not slug:
        return f"tvl/fees/revenue: not fetched ({unavailable or 'no DeFiLlama match'})"
    retrieved = [
        label
        for label, key in (("tvl", "tvl_usd"), ("fees", "fees_30d_usd"), ("revenue", "revenue_30d_usd"))
        if key in defi_facts
    ]
    clause = (
        f"DeFiLlama '{slug}' {_trim_stamp(defi_facts.get('fetched_at'))} "
        f"({', '.join(retrieved) or 'nothing'}"
    )
    if unavailable:
        clause += f"; {unavailable} — absent, not zero"
    return clause + ")"


def build_case_context(
    project_name: str,
    resolved_info: JSONObject,
    defi_facts: JSONObject | None = None,
) -> JSONObject:
    """Build canonical case context from protocol resolution data.

    ``defi_facts`` is the result of ``tools.defillama.fetch_canonical_facts``,
    fetched by the orchestrator immediately before this call. It is a parameter
    rather than an await inside this function so the signature stays synchronous
    and every existing caller keeps working.

    Two properties the callers depend on:

    * **Absent, never zero.** A DeFiLlama figure we did not retrieve does not
      appear as a key at all, and ``_as_of`` says so in prose. ``0.0`` in this
      dict always means a real zero. The trap is concrete: DeFiLlama answers
      ``GET /tvl/plasma`` with HTTP 200 and an empty body, so the obvious
      ``float(body or 0)`` reports a chain with no TVL series as a chain with no
      value locked.
    * **Nothing extra when nothing is known.** ``_as_of`` and ``_rule`` are added
      only once at least one figure resolved. An all-null baseline is not an
      authority and must not be dressed as one.
    """
    now = datetime.now(timezone.utc)
    resolved_info = _as_mapping(resolved_info)
    price_data = _as_mapping(resolved_info.get("_price_data"))
    token_data = _as_mapping(resolved_info.get("_token_data"))
    defi = _as_mapping(defi_facts)

    metrics: dict[str, Any] = {
        "price_usd": price_data.get("price"),
        "market_cap_usd": price_data.get("market_cap") or token_data.get("market_cap_usd"),
        "volume_24h_usd": price_data.get("volume_24h") or token_data.get("total_volume_usd"),
        "fdv_usd": token_data.get("fully_diluted_valuation"),
        "circulating_supply": token_data.get("circulating_supply"),
        "total_supply": token_data.get("total_supply"),
        "max_supply": token_data.get("max_supply"),
    }
    coingecko_resolved = any(value is not None for value in metrics.values())

    for key in ("tvl_usd", "fees_30d_usd", "revenue_30d_usd"):
        if key in defi:
            metrics[key] = defi[key]

    if coingecko_resolved or any(k in defi for k in ("tvl_usd", "fees_30d_usd", "revenue_30d_usd")):
        metrics["_as_of"] = "; ".join((
            f"CoinGecko spot {_stamp(now)} (price, market cap, volume, supply)"
            if coingecko_resolved
            else "price/market cap/volume/supply: not fetched",
            _defillama_clause(defi),
        ))
        # `_rule` deliberately not attached: base.py's CANONICAL METRICS heading
        # now carries that instruction, and the heading is the better home — it
        # cannot be mistaken for a metric. Keeping both cost ~114 tokens per
        # agent, i.e. ~1,700 per evaluation, to say the same thing twice.
        # BASELINE_RULE is retained as the single source of that wording.

    return {
        "case_time": now.isoformat(),
        "project_name": project_name,
        # Other names this project is written under, for the within-run prose
        # check. A claim whose entity does not resolve is dropped, and a
        # first-time project is in no alias table, so without these the check is
        # blind to the majority of a run's claims — the ones about the subject.
        # Not rendered to any model: BaseAgent reads only `canonical_metrics`
        # and `case_time` out of this dict, so this costs zero tokens.
        "project_aliases": sorted(
            {
                str(resolved_info.get(key) or "").strip()
                for key in ("ticker", "symbol", "coingecko_id", "defillama_slug")
            }
            - {""}
        ),
        "canonical_metrics": metrics,
        "evaluation_date": now.strftime("%Y-%m-%d"),
        "data_snapshot_note": "Canonical baseline metrics as of evaluation_date. Flag discrepancies with external sources.",
    }


async def fetch_canonical_defi_facts(
    project_name: str, resolved_info: JSONObject
) -> JSONObject:
    """Fetch the DeFiLlama half of the baseline for ``build_case_context``.

    A thin seam so the orchestrator does not have to know which module speaks to
    DeFiLlama. Imported lazily for the same reason ``orchestrator._resolve_protocol``
    imports the registry lazily: ``app.tools`` pulls in the whole tool package at
    import time.
    """
    from app.tools.defillama import fetch_canonical_facts

    resolved_info = _as_mapping(resolved_info)
    return dict(
        await fetch_canonical_facts(
            project_name,
            coingecko_id=str(resolved_info.get("coingecko_id") or ""),
            slug_hint=str(resolved_info.get("defillama_slug") or ""),
        )
    )


def reconcile_data(
    agent_outputs: dict[str, JSONObject],
    case_context: JSONObject,
    scope: str = "run",
) -> JSONObject:
    """Flag numerical inconsistencies across the agent outputs of one run.

    Two independent passes, both deterministic, both free of model calls:

    * the **structured** pass over numeric JSON leaves (unchanged — see
      ``_extract_metrics``), reported as ``inconsistencies``;
    * the **prose** pass over the text fields, reported as ``contradictions``.

    ``scope`` is a label for the caller's own bookkeeping — the orchestrator
    runs this twice, once over the data layer and once over the whole run
    including the Report Writer — and is echoed back in the result so a
    persisted reconciliation says which pass produced it.

    The prose pass is wrapped: it does strictly more work than this function
    used to (regex extraction over every string in fifteen agent outputs), and
    a reconciliation that can abort an evaluation is worse than no
    reconciliation. Any failure degrades to the structured-only result.
    """
    inconsistencies = []
    metrics_by_agent = {
        agent_name: extracted
        for agent_name, output in agent_outputs.items()
        if isinstance(output, Mapping) and (extracted := _extract_metrics(output))
    }

    metric_values = {}
    for agent, metrics in metrics_by_agent.items():
        for key, val in metrics.items():
            metric_values.setdefault(_group_metric_key(key), []).append({"agent": agent, "key": key, "value": val})

    for metric_key, entries in metric_values.items():
        if len(entries) < 2:
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, b = entries[i], entries[j]
                if a["agent"] == b["agent"]:
                    # Reconciliation is a cross-agent check. Leaf grouping can
                    # put two of one agent's own figures in the same group
                    # (a per-chain breakdown); that is not a disagreement.
                    continue
                divergence = _relative_divergence(a["value"], b["value"])
                if divergence > DIVERGENCE_THRESHOLD:
                    inconsistencies.append({
                        "metric": metric_key,
                        "agent_a": a["agent"],
                        "value_a": a["value"],
                        "agent_b": b["agent"],
                        "value_b": b["value"],
                        "divergence_pct": round(divergence * 100, 1),
                    })

    try:
        claims = _run_claims(agent_outputs, case_context)
        contradictions, uncorroborated = _detect_contradictions(claims)
        claim_stats = {
            "prose_claims_extracted": len(claims),
            "prose_agents_with_claims": len({c.source_agent for c in claims}),
            "uncorroborated_candidates": uncorroborated,
        }
    except Exception as exc:  # regex, import, malformed output — anything
        logger.warning("Prose reconciliation unavailable (non-fatal): %s", exc)
        contradictions = []
        claim_stats = {
            "prose_claims_extracted": 0,
            "prose_agents_with_claims": 0,
            "uncorroborated_candidates": [],
        }

    parts = []
    if inconsistencies:
        parts.append("%d inconsistencies" % len(inconsistencies))
    if contradictions:
        parts.append("%d contradictions" % len(contradictions))

    return {
        "case_time": case_context.get("case_time"),
        "scope": scope,
        "canonical_metrics": case_context.get("canonical_metrics", {}),
        "inconsistencies_found": len(inconsistencies),
        "inconsistencies": inconsistencies[:10],
        "contradictions_found": len(contradictions),
        "contradictions": [c.to_json() for c in contradictions[:INTRA_RUN_MAX_FINDINGS]],
        **claim_stats,
        "status": "CLEAN" if not parts else "WARNING: " + ", ".join(parts),
    }


def _flatten(value: object, prefix: str = "") -> list[tuple[str, object]]:
    """Every scalar in a nested structure, with its path and its leaf name.

    QA-022: this descended into dicts only. A per-chain breakdown — the natural
    shape for TVL — was invisible to reconciliation entirely.
    """
    items: list[tuple[str, object]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (Mapping, list, tuple)):
                items.extend(_flatten(child, path))
            else:
                items.append((path, child))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(child, (Mapping, list, tuple)):
                items.extend(_flatten(child, path))
            else:
                items.append((path, child))
    return items


def _as_metric_number(value: object) -> float | None:
    """A positive finite number, or None.

    QA-021: ``isinstance(val, (int, float))`` is True for bool, so a flag like
    ``{"tvl_verified": true}`` was extracted as the number 1 and compared
    against another agent's real TVL — a 499,999,900% "inconsistency" that
    buries every real disagreement under the ten-item cap.

    QA-022: models routinely quote their figures, and ``"100"`` was ignored, so
    the check was silently skipped for any agent that did.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _leaf_name(path: str) -> str:
    """The final component of a flattened path, separators removed.

    QA-019: metrics were grouped by the *whole* dotted path, so two agents had
    to agree on the exact nesting and the exact spelling before anything was
    compared. The eight data agents are independent by design (CONTRACTS §4.2)
    and each shapes its own JSON, so in practice nothing was ever compared and
    reconcile_data reported CLEAN for every evaluation.

    Grouping on the leaf keeps genuinely different metrics apart —
    "total_supply" and "circulating_supply" remain distinct — while letting
    "metrics.tvl" and "protocol_data.tvl" meet. It will also group a nested
    comparison figure (a competitor's market cap) with a top-level one; that is
    a false positive in a report of *candidate* disagreements, and strictly
    better than the previous behaviour of never comparing anything.
    """
    leaf = path.rsplit(".", 1)[-1]
    leaf = leaf.split("[", 1)[0]
    return leaf.lower().replace("_", "").replace("-", "")


def _extract_metrics(output: Mapping[str, Any]) -> dict[str, float]:
    extracted: dict[str, float] = {}
    for path, value in _flatten(output):
        number = _as_metric_number(value)
        if number is None:
            continue
        if any(term in _leaf_name(path) for term in METRIC_TERMS):
            extracted[path] = number
    return extracted


def _group_metric_key(key: str) -> str:
    return _leaf_name(key)


def _relative_divergence(a: float, b: float) -> float:
    """Symmetric relative difference.

    QA-020: this was ``abs(a - b) / a``, so which value landed in ``a`` — the
    iteration order the orchestrator happened to gather results in — decided
    whether the pair tripped the threshold. (100, 125) is 25% one way and 20%
    the other. The same evaluation run twice could report different counts.
    """
    scale = max(abs(a), abs(b))
    if scale == 0:
        return 0.0
    return abs(a - b) / scale


# ---------------------------------------------------------------------------
# Within-run prose reconciliation
# ---------------------------------------------------------------------------
#
# THE DEFECT, MEASURED
#
# Live GMX evaluation, 15 agents. `_extract_metrics` found 5 numbers in total,
# four of them inside `tokenomics_analyst`. Nothing could be compared, so
# `reconcile_data` returned `inconsistencies_found: 0` for that run and for
# every other run in the corpus — the figures agents actually disagree about
# are in `summary`, `key_findings` and the Report Writer's 24 sections, as
# sentences. Reading the prose takes the same corpus from 59 structured numbers
# across 16 evaluations to 132 comparable claims.
#
# The contradiction that yield buys, from Aave evaluation c1479a94:
#
#     competitive_intel   "Aave dominates DeFi lending with $25.7B TVL"
#     onchain_analyst     "$25.7B TVL across 20+ chains"
#     tokenomics_analyst  "$25.6B TVL across 20+ chains"
#     governance_analyst  "...into Aave with $61.9B TVL across 20+ chains"
#     maturation_scorer   "$61.9B TVL across 20+ chains"
#     risk_officer        "$61.9B TVL across 20+ chains demonstrates maturity"
#
# Two irreconcilable figures for one metric, three agents behind each, the same
# "across 20+ chains" qualifier on both, and no agent anywhere saying they are
# different quantities. The Report Writer put $25.7B in its executive summary
# and $61.9B in its project overview; the Chair then decided on $25.7B, never
# told that half the committee was at $61.9B.
#
# WHY THIS RE-KEYS `evaluation_id` INSTEAD OF EXTENDING consistency.py
#
# `consistency.detect_conflicts` compares claims only across *different*
# `evaluation_id`s, on purpose: intra-run drift is this module's job and
# flagging it there would double-report it. Within one run the interesting axis
# is not the evaluation, it is the *source* — which agent, and for the Report
# Writer which section. Setting `evaluation_id` to "<agent>::<json path>" makes
# the cross-report scoping rule express exactly the within-run one, with no
# change to a module this branch does not own. Two figures from one section are
# never compared (that is a sentence-level parse question, and
# `_drop_comparatives` already handles it); two sections of one agent are.
#
# WHAT IS DELIBERATELY NOT REPORTED
#
# The cross-report audit's own catalogue of near-misses applies here too, and
# within one evaluation there are more of them, because fifteen agents restate
# the same handful of facts:
#
# * **The same quantity at different dates.** Handled upstream:
#   `_resolve_period` gives an explicitly dated figure its own period, and
#   `_periods_comparable` refuses to compare periods that do not contain one
#   another. "44% in January 2026" and "70-80% now" never meet.
# * **Different denominators.** "GMX volume" is perps, or spot, or both.
#   Measured: `onchain_analyst` says $100-200M daily (protocol perp notional)
#   and `risk_officer` says ~$3M daily (GMX-the-token across CEX/DEX venues).
#   Both are true. That pair is 50x apart, which is why the bar below is not a
#   percentage.
# * **Overlapping hedged ranges.** `Claim.interval` widens a hedged figure by
#   10% and a precise one by 2%, and only disjoint intervals are considered.
# * **A figure and its component.** GMX TVL ~$300M total against $174.88M for
#   V2 Perps alone, and ~$198M for the Arbitrum share. 2x, and not a defect.
#
# The date-attribution rule from the cross-report audit — the same value pinned
# to two different periods — is deliberately NOT run within a run. Across runs
# it is a strong signal. Within one run every undated figure inherits the same
# inferred period (the run's own timestamp), so the rule fires whenever any
# agent restates a dated figure without repeating its date. Measured on GMX it
# produced three findings, all three of that shape, none of them defects.

#: How many DISTINCT AGENTS must assert a value before it can be one side of a
#: reported contradiction.
#:
#: THIS REPLACED A MAGNITUDE THRESHOLD, AND THE REPLACEMENT IS THE WHOLE POINT.
#:
#: The first version of this module gated on how far apart two figures were,
#: calibrated against a contradiction that turned out not to exist: the GMX
#: report's "$3,341,200 purchased over 30 days" is a *buyback*, and reading it
#: as a 30-day trading volume was an extraction defect, now fixed in
#: ``_binding_is_sound``. With that gone, the corpus says plainly that magnitude
#: does not separate signal from noise — it ranks them backwards. Measured over
#: all 16 persisted evaluations:
#:
#:     50.0x  GMX 24h volume: ~$3M (token, across CEX/DEX) vs ~$150M (V2 perps)
#:             -> two different quantities. FALSE.
#:      2.4x  Aave TVL: $25.7B (three agents) vs $61.9B (three agents), both
#:             phrased "across 20+ chains", the Report Writer using $25.7B in
#:             section 1 and $61.9B in section 2 -> TRUE.
#:
#: Any threshold admitting the true one admits the false one twenty times over.
#:
#: What does separate them is who is speaking. A figure only one agent ever
#: states, differing from a figure several agents state, is almost always that
#: agent measuring something adjacent and labelling it loosely — "GMX V2 Perps
#: TVL ($174.88M)" against five agents' ~$300M, "~$3M aggregate 24h volume"
#: against three agents' V2 perp volume, a footnote reading "Hyperliquid 70%+
#: share, $2.8B TVL" against two agents' ~$6.66B. All three are scope or
#: labelling differences and all three are suppressed by this rule. When two or
#: more *independent* agents each arrive at the same incompatible value, the run
#: is holding two beliefs rather than one agent using a private definition.
#:
#: Distinct agents, not distinct mentions: a Report Writer restating its own
#: figure across four sections is one voice, not four.
#:
#: THE COST, STATED PLAINLY. A wrong figure invented by exactly one agent is
#: never reported. That includes the Report Writer inventing one — the shape
#: this pass was originally commissioned to catch. Those clusters are still
#: computed and returned under ``uncorroborated_candidates``; they are simply
#: not put in front of the Chair, because on this corpus every one of them was
#: a scope difference and crying wolf is how a guard gets ignored.
INTRA_RUN_MIN_CORROBORATION = 2

INTRA_RUN_MAX_FINDINGS = 3

#: Hard ceiling on the rendered block, mirroring consistency.WARNING_CHAR_BUDGET.
#: Paid once per evaluation on one Opus call, and only when something is found.
INTRA_RUN_RENDER_BUDGET = 1400

#: Strings shorter than this carry no extractable claim and cost time to scan.
_MIN_PROSE_CHARS = 20

#: Agents named per camp before the rest are summarised as a count.
_MAX_RENDERED_AGENTS = 4

#: A metric phrase *preceding* its value may not be reached across one of
#: these. See `_binding_is_sound`.
_BACKWARD_CLAUSE_BREAK = re.compile(r"[,;]|[—–]|\s-\s")

#: Text ending in a quantity: a number, optionally a magnitude suffix, and at
#: most one trailing word. "Buybacks: 103,764 GMX" ends in one; "small market
#: cap" and "GMX V2 Perps TVL" do not. The one-word limit is what keeps "V2" in
#: "GMX V2 Perps TVL" from making that phrase look like a quantity.
_TRAILING_QUANTITY = re.compile(
    r"\d[\d,.]*\s*(?:[KkMmBbTt]\b)?\s*(?:[A-Za-z][A-Za-z0-9.]{0,14})?$"
)

#: "…% of <metric>" — the metric is a denominator here, not a label. Anchored at
#: the end so it tests the text immediately before the metric phrase.
_DENOMINATOR_PREFIX = re.compile(r"%\s*(?:of|de)\s+$|\bof\s+$", re.I)

#: Quantities this corpus states in dollars that the cross-report audit does
#: not track. A figure closer to one of these than to its own metric label is
#: governed by it.
#:
#: PRECISION-ONLY KNOB. Every term here can only *remove* claims, never create
#: or misattribute one — the exact mirror of ``SEED_ENTITY_ALIASES``, which can
#: only add them. Widening it is therefore always safe and never urgent.
#:
#: Each entry is a phrase found in the live corpus, not a guess:
#:   buyback/purchased  "Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days"
#:   supply/vesting     "23.8% of total supply (238M HYPE, ~$19.4B FDV) vesting"
#:   unlock             "~14.175M HYPE tokens unlock August 29, 2026"
#:   staked/staking     "staking ~$45M", "sGMX on Arbitrum shows 7,424,699 staked"
#:   treasury           "beyond DeFiLlama's $25.72M snapshot"
#:   fees/revenue       "Annualized fees are ~$31.92M ... revenue ~$11.8-13.1M"
#:   float              "~4.5% of float"
#:   price/ATH/ATL      "trading at $7.20", "92.1% below the $91.07 ATH"
#:   inflows            "record ETF inflows"
_FOREIGN_QUANTITY = re.compile(
    r"\b(?:"
    r"buy[- ]?backs?|repurchas\w*|purchas\w*|bought|acquired|"
    r"unlock\w*|vest\w*|emission\w*|"
    r"stak\w*|burn\w*|"
    r"treasury|reserves?|"
    r"revenues?|fees?|"
    r"float|circulating|supply|"
    r"inflows?|outflows?|deposits?|withdrawals?|collateral|liquidations?|"
    r"prices?|ATH|ATL|trading\s+at"
    r")\b",
    re.I,
)


class _RunClaim:
    """One extracted claim plus the agent and JSON path it came from.

    A thin wrapper rather than a subclass: ``consistency.Claim`` is a frozen
    dataclass in a module this branch does not own, and the two extra fields
    are bookkeeping for rendering, not part of the claim.
    """

    __slots__ = ("claim", "source_agent", "source_path")

    def __init__(self, claim: Any, source_agent: str, source_path: str) -> None:
        self.claim = claim
        self.source_agent = source_agent
        self.source_path = source_path

    @property
    def source(self) -> str:
        return f"{self.source_agent} {self.source_path}"

    @property
    def mid(self) -> float:
        return (self.claim.lo + self.claim.hi) / 2.0


class _Camp:
    """One value that some set of agents assert, and who asserts it.

    Members are merged by interval *overlap*, not by literal equality: "$25.7B"
    and "$25.6B" are one belief stated twice, and counting them as two would
    make a camp look less corroborated than it is.
    """

    __slots__ = ("members",)

    def __init__(self, members: list[_RunClaim]) -> None:
        self.members = members

    @property
    def agents(self) -> set[str]:
        return {m.source_agent for m in self.members}

    @property
    def corroborated(self) -> bool:
        return len(self.agents) >= INTRA_RUN_MIN_CORROBORATION

    @property
    def mid(self) -> float:
        return sum(m.mid for m in self.members) / len(self.members)

    @property
    def label(self) -> str:
        return self.members[0].claim.raw


class _Contradiction:
    """Two corroborated camps for one (entity, metric, period) that cannot both hold."""

    def __init__(self, entity: str, metric: str, label: str, unit: str,
                 camps: list[_Camp]) -> None:
        self.entity = entity
        self.metric = metric
        self.label = label
        self.unit = unit
        self.camps = sorted(camps, key=lambda c: c.mid)

    @property
    def members(self) -> list[_RunClaim]:
        return [m for camp in self.camps for m in camp.members]

    @property
    def ratio(self) -> float:
        lo, hi = self.camps[0].mid, self.camps[-1].mid
        return hi / lo if lo > 0 else float("inf")

    @property
    def period(self) -> str:
        return " / ".join(sorted({m.claim.period for m in self.members}))

    @property
    def split_by_one_agent(self) -> list[str]:
        """Agents that are on both sides of the split — they contradict themselves.

        The Report Writer landing here is the report contradicting itself, which
        is the case this whole pass exists for. On the Aave run it did: §1 says
        $25.7B, §2 says $61.9B.
        """
        counts: dict[str, int] = {}
        for camp in self.camps:
            for agent in camp.agents:
                counts[agent] = counts.get(agent, 0) + 1
        return sorted(name for name, seen in counts.items() if seen > 1)

    def to_json(self) -> JSONObject:
        return {
            "entity": self.entity,
            "metric": self.metric,
            "label": self.label,
            "unit": self.unit,
            "period": self.period,
            "ratio": round(self.ratio, 1),
            "self_contradicting_agents": self.split_by_one_agent,
            "camps": [
                {
                    "value": camp.label,
                    "agents": sorted(camp.agents),
                    "sources": [m.source for m in camp.members],
                    "quote": camp.members[0].claim.quote[:240],
                }
                for camp in self.camps
            ],
        }


def _prose_leaves(output: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Every string leaf in an agent output, with its JSON path.

    Reuses ``_flatten`` so the traversal rules — including QA-022's descent
    into lists — are shared with the structured pass rather than duplicated.
    """
    for path, value in _flatten(output):
        if isinstance(value, str) and len(value) >= _MIN_PROSE_CHARS:
            yield path, value


def _run_aliases(case_context: Mapping[str, Any]) -> dict[str, str]:
    """The cross-report alias map, plus this project's own names.

    ``consistency.SEED_ENTITY_ALIASES`` covers the third parties the committee
    writes about, but the project currently under evaluation may never have been
    seen before, and a claim whose entity does not resolve is dropped. Adding
    the subject's own names is what makes claims *about the subject* — the
    majority of a run's claims — visible at all.

    Aliases shorter than three characters are refused. ``_entity_mentions``
    matches a non-ambiguous alias case-insensitively, and a two-letter token
    matches English.
    """
    from app.knowledge.consistency import _build_alias_map

    project = str(case_context.get("project_name") or "").strip()
    extra: list[tuple[str, str]] = []
    if project:
        for alias in [project, *case_context.get("project_aliases", [])]:
            text = str(alias or "").strip()
            if len(text) >= 3:
                extra.append((text, project))
    return _build_alias_map(extra)


def _run_claims(
    agent_outputs: Mapping[str, Any], case_context: Mapping[str, Any]
) -> list[_RunClaim]:
    """Every comparable claim in one run's prose, keyed by agent and path."""
    from app.knowledge.consistency import extract_claims

    aliases = _run_aliases(case_context)
    project = str(case_context.get("project_name") or "unknown")
    report_date = _case_datetime(case_context)

    out: list[_RunClaim] = []
    for agent_name, output in sorted(agent_outputs.items()):
        if not isinstance(output, Mapping):
            continue
        for path, text in _prose_leaves(output):
            for claim in extract_claims(
                text,
                # See the module note above: the source, not the evaluation, is
                # the axis a within-run check compares across.
                evaluation_id=f"{agent_name}::{path}",
                report_project=project,
                section=f"{agent_name} {path}",
                report_date=report_date,
                aliases=aliases,
            ):
                if _binding_is_sound(claim):
                    out.append(_RunClaim(claim, agent_name, path))
    return out


def _case_datetime(case_context: Mapping[str, Any]) -> datetime:
    """The run's own timestamp, for the undated-claim fallback period."""
    raw = str(case_context.get("case_time") or "")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _binding_is_sound(claim: Any) -> bool:
    """Whether the metric label the extractor chose actually governs this figure.

    ``consistency._classify_metric`` binds a metric phrase to a value by
    adjacency: within 30 characters, no digit in the gap, either direction. That
    is right most of the time and wrong in three ways this corpus produces
    constantly. Each rule below removes one of them, and each was written after
    reading the sentence it fires on.

    **1. A parenthetical that restates a preceding quantity.** This is the one
    that matters, and it is the defect that made the first version of this
    module report a contradiction that does not exist::

        "Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days"

    $3,341,200 is 103,764 GMX priced in dollars — a *buyback*, restated in the
    bracket. "over 30 days" is thirteen characters away with no digit between,
    so the extractor read it as a 30-day trading volume and set it against the
    same report's ~$2.8B of actual volume: an 838x "contradiction" between a
    buyback and a volume, neither of which disagrees with anything. The same
    shape, in the same corpus::

        "23.8% of total supply (238M HYPE, ~$19.4B FDV) vesting linearly"

    $19.4B is the value of the vesting tranche. The report says elsewhere, and
    correctly, "FDV ($78B) is 4.3x market cap ($18.2B)".

    The rule: a figure inside a bracket is a restatement, not an independent
    claim, when a number precedes it *inside* that bracket, or when the text
    immediately before the bracket ends in one. A bracket that follows a bare
    label — "market cap ($75M)", "GMX V2 Perps TVL ($174.88M)", "FDV ($78B)" —
    is the ordinary way this corpus states a figure and is untouched, which is
    why the test is about the preceding *quantity* and not about the bracket.

    **2. A nearer term naming a different quantity.** If some other metric's
    phrase, or a quantity this audit does not track at all, sits closer to the
    figure than the label that classified it, the closer one governs. The rival
    metric phrases are derived from ``CANONICAL_METRICS``; the untracked
    quantities are ``_FOREIGN_QUANTITY``.

    **3. A label reaching backwards across a clause break.**::

        "GMX is in a daily uptrend, trading at $7.20 — above all three EMAs"

    "daily" labels the uptrend. Forward binding is safe — English writes "$6.66B
    TVL", "GMX's ~$2.8B 30-day volume" — so this applies in one direction only.

    A claim whose label cannot be re-derived from ``claim.quote`` at all is
    kept: that is the signature of the extractor's continuation rule ("up from
    36.4% in January 2026"), where the metric was stated several clauses back.

    All of this is a post-filter rather than an edit to ``_classify_metric``,
    which belongs to the cross-report audit and whose recall trade-offs are not
    this one's. See the module note for the exact change that module needs if
    these rules are ever adopted there.
    """
    located = _locate_value(claim)
    if located is None:
        return True
    quote, start, end = located

    if _restates_a_preceding_quantity(quote, start):
        return False

    own, denominators = _own_metric_bindings(quote, start, end, claim)
    if not own:
        # No re-derivable label. Normally that means the extractor's
        # continuation rule supplied one from several clauses back, which this
        # function cannot judge and must not veto. But if the only phrase in
        # reach was a denominator reference, the label is accounted for and it
        # does not govern this figure.
        return not denominators

    rivals = [d for d in (_nearest_rival(quote, start, end, claim.metric),) if d is not None]
    rivals += denominators
    if rivals and min(rivals) < min(distance for distance, _ in own):
        return False

    return any(not crosses_break for _distance, crosses_break in own)


def _locate_value(claim: Any) -> tuple[str, int, int] | None:
    """``(quote, start, end)`` for the literal this claim was parsed from."""
    quote, raw = claim.quote, claim.raw
    start = quote.find(raw)
    if start < 0:
        return None
    return quote, start, start + len(raw)


def _restates_a_preceding_quantity(quote: str, start: int) -> bool:
    """Whether the figure at ``start`` sits in a bracket restating a quantity."""
    from app.knowledge.consistency import _ANY_NUMBER

    opened = max(quote.rfind("(", 0, start), quote.rfind("[", 0, start))
    if opened < 0:
        return False
    # A bracket that closed before the figure is not the figure's bracket.
    closed = min(
        (index for index in (quote.find(")", opened), quote.find("]", opened)) if index >= 0),
        default=-1,
    )
    if 0 <= closed < start:
        return False
    if _ANY_NUMBER.search(quote[opened + 1:start]):
        return True
    return bool(_TRAILING_QUANTITY.search(quote[:opened].rstrip()))


def _own_metric_bindings(
    quote: str, start: int, end: int, claim: Any
) -> tuple[list[tuple[int, bool]], list[int]]:
    """How this claim's own label could reach the figure.

    Returns ``(bindings, denominator_distances)``. A binding is
    ``(distance, crosses_backward_break)``. A *denominator* is a phrase that
    names the metric only as the thing a percentage is taken of::

        "~14.175M HYPE unlock ... representing ~2.7% of market cap (~$1.16B)"

    $1.16B is 2.7% of the market cap, not the market cap. The surface form is
    identical to the legitimate "small market cap ($75M)", so the bracket rule
    cannot separate them; the "of" is the entire signal. This is the false
    positive ``consistency._classify_metric``'s docstring records — "~3.2% of
    MCap" put Hyperliquid's market cap at $589M — surviving in the one shape
    its no-digit-in-the-gap rule does not cover, because here the digits sit
    before the label rather than between the label and the figure.

    Percentage metrics are exempt: "share of on-chain perp volume" is a metric
    name that contains "of", and treating that as a denominator would delete
    every market-share claim in the corpus.
    """
    from app.knowledge.consistency import _ANY_NUMBER, _METRIC_RES, _METRIC_WINDOW

    found: list[tuple[int, bool]] = []
    denominators: list[int] = []
    for key, unit, regex in _METRIC_RES:
        if key != claim.metric or unit != claim.unit:
            continue
        for match in regex.finditer(quote):
            if unit == "usd" and _DENOMINATOR_PREFIX.search(quote[:match.start()]):
                distance = (
                    start - match.end() if match.end() <= start else match.start() - end
                )
                if 0 <= distance <= _METRIC_WINDOW:
                    denominators.append(distance)
                continue
            if match.start() < end and match.end() > start:
                found.append((0, False))  # the figure sits inside the phrase
            elif match.end() <= start:
                gap = quote[match.end():start]
                if len(gap) > _METRIC_WINDOW or _ANY_NUMBER.search(gap):
                    continue
                found.append((start - match.end(), bool(_BACKWARD_CLAUSE_BREAK.search(gap))))
            else:
                gap = quote[end:match.start()]
                if len(gap) > _METRIC_WINDOW or _ANY_NUMBER.search(gap):
                    continue
                found.append((match.start() - end, False))
    return found, denominators


def _nearest_rival(quote: str, start: int, end: int, metric: str) -> int | None:
    """Distance to the nearest term naming a quantity that is not ``metric``."""
    from app.knowledge.consistency import _ANY_NUMBER, _METRIC_RES, _METRIC_WINDOW

    candidates = [(key, regex) for key, _unit, regex in _METRIC_RES if key != metric]
    candidates.append(("_foreign", _FOREIGN_QUANTITY))

    best: int | None = None
    for _key, regex in candidates:
        for match in regex.finditer(quote):
            if match.start() < end and match.end() > start:
                continue
            if match.end() <= start:
                gap, distance = quote[match.end():start], start - match.end()
            else:
                gap, distance = quote[end:match.start()], match.start() - end
            if distance > _METRIC_WINDOW or _ANY_NUMBER.search(gap):
                continue
            if best is None or distance < best:
                best = distance
    return best


def _detect_contradictions(
    claims: Sequence[_RunClaim],
) -> tuple[list[_Contradiction], list[JSONObject]]:
    """Group by (entity, metric) and return ``(findings, uncorroborated)``.

    A pair of claims clashes when they come from different sources, their
    periods are comparable, and their hedged intervals are disjoint. Clashing
    claims are clustered transitively, then the cluster is split into *camps* by
    interval overlap, and a camp counts only if at least
    ``INTRA_RUN_MIN_CORROBORATION`` **distinct agents** assert it.

    A cluster with fewer than two corroborated camps is not discarded — it is
    returned as an uncorroborated candidate, recorded in the result and shown to
    nobody. That keeps the evidence for a later decision without paying prompt
    tokens for it.

    ``_disjoint`` and ``_periods_comparable`` are the cross-report audit's own
    predicates, imported rather than restated: a within-run check that decided
    "overlapping" or "same period" differently from the cross-run one would
    manufacture disagreements between the two audits.
    """
    from app.knowledge.consistency import (
        CANONICAL_METRICS,
        _disjoint,
        _periods_comparable,
    )

    buckets: dict[tuple[str, str], list[_RunClaim]] = {}
    for item in claims:
        buckets.setdefault((item.claim.entity, item.claim.metric), []).append(item)

    findings: list[_Contradiction] = []
    uncorroborated: list[JSONObject] = []

    for (entity, metric), group in sorted(buckets.items()):
        for cluster in _cluster_clashes(group, _disjoint, _periods_comparable):
            camps = _camps(cluster, _disjoint)
            solid = [camp for camp in camps if camp.corroborated]
            # The surviving camps must still contradict each other. Dropping an
            # uncorroborated camp can leave two that overlap, and two camps that
            # overlap are agreement.
            solid = [
                camp
                for i, camp in enumerate(solid)
                if any(
                    _disjoint(camp.members[0].claim, other.members[0].claim)
                    for j, other in enumerate(solid)
                    if i != j
                )
            ]
            label = str(CANONICAL_METRICS.get(metric, {}).get("label", metric))
            if len(solid) >= 2:
                findings.append(
                    _Contradiction(entity, metric, label, cluster[0].claim.unit, solid)
                )
            else:
                uncorroborated.append(
                    {
                        "entity": entity,
                        "metric": metric,
                        "reason": "no two values are asserted by %d or more agents each"
                        % INTRA_RUN_MIN_CORROBORATION,
                        "values": [
                            {"value": camp.label, "agents": sorted(camp.agents)}
                            for camp in camps
                        ],
                    }
                )

    return sorted(findings, key=lambda f: -f.ratio), uncorroborated


def _cluster_clashes(
    group: Sequence[_RunClaim], disjoint: Any, periods_comparable: Any
) -> list[list[_RunClaim]]:
    """Transitively cluster the claims in one bucket that clash pairwise."""
    clusters: list[list[_RunClaim]] = []
    for i, a in enumerate(group):
        for b in group[i + 1:]:
            if a.claim.evaluation_id == b.claim.evaluation_id:
                continue
            if not periods_comparable(a.claim.period, b.claim.period):
                continue
            if not disjoint(a.claim, b.claim):
                continue
            joined = [c for c in clusters if a in c or b in c]
            if joined:
                target = joined[0]
                for other in joined[1:]:
                    target.extend(x for x in other if x not in target)
                    clusters.remove(other)
                for x in (a, b):
                    if x not in target:
                        target.append(x)
            else:
                clusters.append([a, b])
    return clusters


def _camps(cluster: Sequence[_RunClaim], disjoint: Any) -> list[_Camp]:
    """Split a cluster into camps of mutually overlapping values."""
    camps: list[list[_RunClaim]] = []
    for member in sorted(cluster, key=lambda m: m.mid):
        for camp in camps:
            if not disjoint(camp[0].claim, member.claim):
                camp.append(member)
                break
        else:
            camps.append([member])
    return [_Camp(members) for members in camps]


def render_contradictions(
    reconciliation: Mapping[str, Any], budget: int = INTRA_RUN_RENDER_BUDGET
) -> str:
    """The within-run findings as an adjudicator should read them.

    Returns "" when there is nothing to say, so the caller can splice it in
    unconditionally and a clean evaluation pays exactly zero tokens — which is
    most evaluations. Bounded, worst-first, and it names the source of the
    figure that disagrees rather than only asserting that a disagreement exists:
    "report_writer 5_on_chain_metrics says X" is actionable, "the committee
    disagrees with itself" is not.
    """
    findings = list(reconciliation.get("contradictions") or [])
    if not findings:
        return ""

    header = (
        "=== CONTRADICTIONS INSIDE THIS EVALUATION ===\n"
        "Two or more agents in this run independently asserted incompatible values "
        "for the figures below. This is arithmetic on their own text, not a "
        "judgement about which side is right — and both sides have corroboration, "
        "so neither is a stray. Resolve each one before relying on either value, "
        "and do not build a signpost or a trigger on a figure listed here.\n"
    )
    blocks: list[str] = []
    used = len(header)
    for finding in findings[:INTRA_RUN_MAX_FINDINGS]:
        block = _render_one(finding)
        if used + len(block) + 1 > budget:
            break
        blocks.append(block)
        used += len(block) + 1
    if not blocks:
        return ""
    return header + "\n".join(blocks)


def _render_one(finding: Mapping[str, Any]) -> str:
    camps = list(finding.get("camps") or [])
    lines = [
        "- {entity} — {label} ({period}): {n} incompatible figures, {ratio}x apart.".format(
            entity=finding.get("entity", "?"),
            label=finding.get("label", finding.get("metric", "?")),
            period=finding.get("period", "?"),
            n=len(camps),
            ratio=finding.get("ratio", "?"),
        )
    ]
    for camp in camps:
        agents = camp.get("agents") or []
        shown = ", ".join(agents[:_MAX_RENDERED_AGENTS])
        if len(agents) > _MAX_RENDERED_AGENTS:
            shown += f" +{len(agents) - _MAX_RENDERED_AGENTS} more"
        lines.append(f"    {camp.get('value', '?')}  — {shown}")
    both = finding.get("self_contradicting_agents") or []
    if both:
        lines.append(
            "    NOTE: %s state both. The report you are reading is inconsistent "
            "with itself here." % ", ".join(both)
        )
    return "\n".join(lines)
