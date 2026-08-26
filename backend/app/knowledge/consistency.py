"""Cross-report consistency audit.

The defect this exists to catch, verbatim from the live corpus (two evaluations
seventeen minutes apart on 2026-08-25):

    Hyperliquid report, 7_competitive_landscape:
        "Hyperliquid holds ~44% of on-chain perp volume as of mid-2026, up from
         36.4% in January 2026, on $6.66B TVL and ~$172B 30-day volume"

    GMX report, 7_competitive_landscape:
        "Hyperliquid commands 70-80%+ of on-chain perp volume (44% as of Jan
         2026 rising through mid-2026) with ~$6.66B TVL and processes ~$30B+
         daily / ~$245B over 30 days"

Three disagreements about *the same third party*, and note the shape of the
second one: the **same 44% figure** is attached to **two different dates**. The
GMX report then builds a decision trigger on its own number —
``24_signposts_to_monitor``: "Hyperliquid share: a material loss of its 70-80%
dominance ... would upgrade the call". A contradiction between two reports has
silently become a decision rule that cannot fire correctly.

That last point is why this module keys on **the entity a claim is about**, not
the project the report was written for. Keyed on the report's own project, the
GMX report's claims about Hyperliquid are invisible and this defect is missed
entirely.

Scope boundary
--------------
This is the *cross-run* half of the consistency problem. ``agents/reconciliation``
builds a canonical baseline *within* one evaluation and stops drift between the
fifteen agents of a single run. Nothing there can see across runs, because each
run's baseline is discarded when the run ends. This module reads the persisted
corpus and compares run against run. Accordingly, claims are only ever compared
across **different** ``evaluation_id``s — intra-run disagreement is the
reconciliation agent's problem and flagging it here would double-report it.

Architecture: deterministic detection, LLM adjudication only on candidates
--------------------------------------------------------------------------
Three layers, and only the third one costs money:

    1. EXTRACT   (deterministic, free)  prose -> (entity, metric, value, period)
    2. DETECT    (deterministic, free)  disjoint value intervals in one bucket
    3. ADJUDICATE(external / LLM, paid) which claim is wrong, and what is right

An LLM extraction pass over every report was considered and rejected. The cost
argument is real but weaker than it looks — the 16 ``report_writer`` sections
are 163,191 characters, roughly 41k tokens, so a full-corpus extraction pass is
cents, not dollars. Three stronger reasons decided it:

* **Reproducibility.** These findings are an audit record. The same corpus must
  yield the same findings, or a monthly sweep manufactures fresh "findings"
  from unchanged reports forever and idempotency is unachievable in principle.
  A regex is a function; a sampled model is not.
* **Testability today.** The Anthropic budget is exhausted until 2026-09-01.
  A design whose *detection* half cannot be run is a design that ships
  unverified. Everything up to and including the finding, its severity and its
  rendered warning runs with no API key at all.
* **Cost proportional to the problem.** Most numbers in a corpus never conflict.
  Paying a model to read all of them prices the sweep against corpus size;
  adjudicating only the disagreements prices it against the defect count, which
  is what actually needs to be paid for.

The consequence to hold onto: **adjudication is an upgrade, never a
precondition.** A finding is complete, severe and renderable before any paid
call. "These two reports disagree" is established by arithmetic. Only "and this
one is wrong" needs an outside opinion.

The four-step flow (Jacob's requirement)
----------------------------------------
observe -> check -> flag -> check again -> correct if needed

* **observe**  ``run_audit`` -> ``extract_claims`` -> ``detect_conflicts``
* **check**    ``verify_candidate`` against DeFiLlama / CoinGecko. No Anthropic
               key needed. CoinGecko's 429-as-HTTP-200 hazard (CONTRACTS §2.7)
               is handled by reusing ``tools.coingecko._get_with_backoff`` and
               ``body_rate_limited`` rather than issuing a bare request.
* **flag**     ``record_finding`` appends a revision to ``consistency_findings``.
* **check again** ``recheck_finding`` after ``RECHECK_INTERVAL_HOURS``. See the
               section below — this step is not a retry.
* **correct**  ``supersede_finding`` appends a *new* revision. Reports are never
               touched. See "Corrections supersede" below.

What "check again" is for
-------------------------
It is not a retry and it is not a re-read of the reports. The reports are
immutable; reading them a second time returns exactly what it returned the first
time and learns nothing. The second check re-measures **the authoritative
source**, and the question it answers is:

    Did the ground truth move between T0 and T1?

* Ground truth **moved** materially -> the metric is genuinely volatile on this
  timescale, so two reports written minutes or weeks apart can legitimately
  disagree. Classified ``transient``. Severity drops; the warning becomes
  "this figure moves fast, date it" rather than "this report is wrong".
* Ground truth **stable** across T0->T1 and still incompatible with at least one
  claim -> the disagreement cannot be explained by movement. That report was
  wrong when it was written. Classified ``confirmed_error``, full severity, and
  the finding names which side is wrong.
* Ground truth **unreachable** both times -> ``unverified``. The finding stays
  open at reduced severity, because two reports contradicting each other is a
  defect whether or not an oracle is available to referee it.

One class of error deliberately survives this: a **date-attribution** error, the
Hyperliquid 44% case, where the same number is pinned to two different dates. No
spot query against DeFiLlama can adjudicate "was it 44% in January or in June" —
the sources do not serve that history for this metric. Such a finding stays
``unverified`` and keeps full contradiction severity, because the contradiction
is certain even when the referee is absent. Verification adds a correction; it is
never what makes a finding real.

Corrections supersede — history is never rewritten
--------------------------------------------------
CONTRACTS §2.5: past reports are the audit record and for the 18 June cohort they
are the only surviving copy of the reasoning. Nothing in this module issues an
UPDATE or a DELETE against ``reports``, ``agent_outputs`` or ``evaluations``. It
reads them and nothing more.

``consistency_findings`` is itself append-only, for the same reason. A finding is
not a row that changes state; it is a chain of immutable revisions sharing a
``fingerprint``. A re-check appends revision 2. A correction appends revision 3
carrying ``supersedes_id``. Current state is the highest revision per
fingerprint. There is no UPDATE anywhere in this file.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Sequence

from sqlalchemy import text as sql_text

from app.database import async_session

logger = logging.getLogger(__name__)

__all__ = [
    "Claim",
    "Conflict",
    "AuditResult",
    "Verification",
    "CANONICAL_METRICS",
    "RECHECK_INTERVAL_HOURS",
    "AUDIT_EVERY_N_REPORTS",
    "AUDIT_EVERY_N_DAYS",
    "WARNING_CHAR_BUDGET",
    "extract_claims",
    "detect_conflicts",
    "load_corpus",
    "run_audit",
    "verify_candidate",
    "record_finding",
    "recheck_finding",
    "supersede_finding",
    "active_findings",
    "render_active_warnings",
    "audit_is_due",
    "fingerprint_of",
]


# ---------------------------------------------------------------------------
# Policy knobs
# ---------------------------------------------------------------------------

#: "either every 10 reports or monthly" — Jacob's requirement, verbatim.
AUDIT_EVERY_N_REPORTS = 10
AUDIT_EVERY_N_DAYS = 30

#: Gap between the first check and the second. Long enough that a genuinely
#: volatile metric will have moved, short enough that a monthly sweep completes
#: well inside its own period.
RECHECK_INTERVAL_HOURS = 24

#: A metric counts as "moved" between T0 and T1 if it moved more than this.
#: Below it, movement cannot explain a disagreement, so the report was wrong.
GROUND_TRUTH_MOVEMENT_PCT = 5.0

#: Hard ceiling on the rendered warning block. This is the number that gets
#: paid on every agent of every run, so it is a constant and not a suggestion.
WARNING_CHAR_BUDGET = 1400
MAX_RENDERED_WARNINGS = 3


# ---------------------------------------------------------------------------
# Layer 1 — deterministic extraction
# ---------------------------------------------------------------------------
#
# Everything below runs on prose with no model and no network.

_NUM = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"
_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}

#: A leading hedge ("~", "roughly") or a trailing "+" widens the interval later.
_HEDGE = r"(?:~|about |around |roughly |approximately |circa |over |under |nearly |almost )?"

_RANGE_PCT = re.compile(rf"({_HEDGE})({_NUM})\s*[-–—]\s*({_NUM})\s*%(\+)?")
_ONE_PCT = re.compile(rf"({_HEDGE})({_NUM})\s*%(\+)?")
_RANGE_USD = re.compile(
    rf"({_HEDGE})\$\s?({_NUM})\s*([kKmMbBtT])?\s*[-–—]\s*\$?\s?({_NUM})\s*([kKmMbBtT])?(\+)?"
)
_ONE_USD = re.compile(rf"({_HEDGE})\$\s?({_NUM})\s*([kKmMbBtT])?(\+)?")

#: Canonical metric key -> the phrases that mean it, and the unit it must carry.
#: Deliberately narrow. A number whose surrounding text matches nothing here is
#: dropped, not guessed at — see the false-positive discussion in the module
#: docstring. Widening this dict is how the audit is taught a new metric.
CANONICAL_METRICS: dict[str, dict[str, Any]] = {
    "perp_market_share_pct": {
        "unit": "pct",
        "label": "share of on-chain perpetuals volume",
        "patterns": [
            r"(?:of\s+)?(?:on-chain\s+|decentrali[sz]ed\s+)?perp(?:etual)?s?\s*(?:-futures\s*)?(?:market\s+)?(?:share|volume|dominance)",
            r"(?:market\s+)?share\s+of\s+(?:on-chain\s+)?perp",
            r"perp\s+(?:DEX\s+)?(?:market\s+)?share",
        ],
        # DeFiLlama serves perps DEX volume; share is derived from it.
        "verifier": "defillama_perp_share",
    },
    "volume_30d_usd": {
        "unit": "usd",
        "label": "30-day trading volume",
        "patterns": [
            r"30[-\s]?day\s+volume",
            r"30d\s+volume",
            r"volume\s+over\s+30\s+days",
            r"over\s+30\s+days",
            r"monthly\s+volume",
        ],
        "verifier": "defillama_volume_30d",
    },
    "volume_24h_usd": {
        "unit": "usd",
        "label": "daily trading volume",
        "patterns": [
            r"dail(?:y|ies)\s+volume",
            r"volume\s+per\s+day",
            r"24h?\s+volume",
            # Postfix form, "~$30B+ daily". Safe only because the metric window
            # is tight and a number may not intervene; a bare "daily" thirty
            # characters from an unrelated dollar figure will not bind.
            r"\bdaily\b",
        ],
        "verifier": "defillama_volume_24h",
    },
    "tvl_usd": {
        "unit": "usd",
        "label": "total value locked",
        "patterns": [r"\bTVL\b", r"total\s+value\s+locked"],
        "verifier": "defillama_tvl",
    },
    "market_cap_usd": {
        "unit": "usd",
        "label": "market capitalisation",
        "patterns": [r"market\s+cap(?:italisation|italization)?\b", r"\bmcap\b"],
        "verifier": "coingecko_market_cap",
    },
    "fdv_usd": {
        "unit": "usd",
        "label": "fully diluted valuation",
        "patterns": [r"\bFDV\b", r"fully[-\s]diluted"],
        "verifier": "coingecko_fdv",
    },
}

#: Compiled once. Order matters only in that the first match wins, and the
#: dict is ordered most-specific-first.
_METRIC_RES: list[tuple[str, str, re.Pattern[str]]] = [
    (key, spec["unit"], re.compile(pat, re.I))
    for key, spec in CANONICAL_METRICS.items()
    for pat in spec["patterns"]
]

#: Entity aliases beyond what the ``projects`` table supplies. These are the
#: third parties the committee writes about but has never evaluated — exactly
#: the population the Hyperliquid/GMX defect lives in. An unresolvable entity
#: means the claim is DROPPED, never guessed, so this list is a precision knob:
#: adding a name can only add findings, never create misattributed ones.
SEED_ENTITY_ALIASES: dict[str, str] = {
    "hyperliquid": "Hyperliquid",
    "hype": "Hyperliquid",
    "gmx": "GMX",
    "dydx": "dYdX",
    "dydx v4": "dYdX",
    "aster": "Aster",
    "lighter": "Lighter",
    "drift": "Drift",
    "vertex": "Vertex",
    "edgex": "edgeX",
    "jupiter": "Jupiter",
    "aave": "Aave",
    "chainlink": "Chainlink",
    "link": "Chainlink",
    "polkadot": "Polkadot",
    "layerzero": "LayerZero",
    "lombard": "Lombard",
    "plasma": "Plasma",
    "quai": "Quai",
    "ethereum name service": "Ethereum Name Service",
    "ens": "Ethereum Name Service",
    "uniswap": "Uniswap",
    "curve": "Curve",
    "lido": "Lido",
    "ethena": "Ethena",
    "morpho": "Morpho",
    "pendle": "Pendle",
    "geodnet": "GEODNET",
}

#: Ticker-shaped aliases that are also ordinary English or ordinary jargon. Only
#: matched when they appear in an unambiguous ticker context, never bare.
_AMBIGUOUS_ALIASES = {"link", "hype", "drift", "vertex", "lighter", "jupiter", "curve"}

_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+|\n+")

#: Maximum characters between an entity mention and the number attributed to it.
#: Beyond this the attribution is a guess.
_ATTRIBUTION_WINDOW = 160

#: Maximum characters between a *metric phrase* and the number it labels. Much
#: tighter than the entity window, and measured against real prose: "a $75M
#: market cap" (1), "with ~$6.66B TVL" (1), "~$245B over 30 days" (1), "On
#: market cap, Hyperliquid is ~$18.3B" (17).
#:
#: The loose version of this rule was the single largest source of false
#: positives. "~9.92M HYPE (~$589M, ~4.5% of float / ~3.2% of MCap)" put the
#: word MCap within 60 characters of an unlock size, and the audit recorded
#: Hyperliquid's market cap as $589M. Percentage-of-a-metric phrasing is
#: everywhere in this corpus, so the binding has to be adjacency, not proximity.
_METRIC_WINDOW = 30

#: Two claims whose periods were both *inferred from the report date* are only
#: compared when the reports were written within this many days of each other.
#:
#: An undated figure is a claim about the moment of writing. Two undated figures
#: therefore only contradict each other if they were written at about the same
#: moment — which is exactly the Hyperliquid/GMX case, seventeen minutes apart.
#: Without this rule the audit reports Aave's TVL in April and Aave's TVL in
#: June as a contradiction, when the only thing that happened is two months.
#: Explicitly dated claims ("36.4% in January 2026") ignore this entirely and
#: are compared on their stated period however far apart the reports are.
MAX_INFERRED_GAP_DAYS = 14

_ANY_NUMBER = re.compile(r"\d")

#: Continuation cues. A second figure introduced by one of these inherits the
#: metric of the figure before it: "~44% of on-chain perp volume as of mid-2026,
#: **up from** 36.4% in January 2026". The metric phrase is stated once and
#: governs both, which is ordinary English and not something an adjacency rule
#: can see — by the time the reader reaches 36.4% the words "perp volume" are
#: forty characters and a comma behind.
#:
#: Without this the audit misses the second half of every "grew from X to Y"
#: sentence, and those halves are exactly the dated historical figures that
#: date-attribution errors live in.
_CONTINUATION = re.compile(
    r"(?:up from|down from|rising (?:to|from)|falling (?:to|from)|grew (?:to|from)|"
    r"versus|vs\.?|compared (?:to|with)|against|from|to)\s+\S{0,12}$",
    re.I,
)


@dataclass(frozen=True)
class Claim:
    """One ``(entity, metric, value, as-of period, source)`` tuple from prose."""

    entity: str
    metric: str
    unit: str
    lo: float
    hi: float
    hedged: bool
    period: str
    evaluation_id: str
    report_project: str
    section: str
    quote: str
    raw: str
    #: False when the period was inferred from the report's own date rather than
    #: stated in the prose. Governs whether MAX_INFERRED_GAP_DAYS applies.
    period_explicit: bool = True
    #: ISO date of the source report, needed to apply that gap rule.
    report_date: str = ""

    @property
    def interval(self) -> tuple[float, float]:
        """Value interval widened by the hedging the author actually wrote.

        A bare "36.4%" is a precise claim and gets a narrow band. A "~44%" or a
        "70-80%+" is explicitly imprecise and gets a wide one. Applying one flat
        tolerance to both would either flag the honest hedgers or miss the
        precise contradictions.
        """
        tol = 0.10 if self.hedged else 0.02
        span = max(abs(self.lo), abs(self.hi)) * tol
        return (self.lo - span, self.hi + span)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["interval"] = list(self.interval)
        return d


def _f(x: str) -> float:
    return float(x.replace(",", ""))


def _find_values(text: str) -> list[tuple[int, int, str, float, float, bool, str]]:
    """(start, end, unit, lo, hi, hedged, raw) for every number in ``text``.

    Ranges are matched before singletons and singletons overlapping an already
    matched range are discarded, so "70-80%" yields one interval rather than
    two points.
    """
    spans: list[tuple[int, int, str, float, float, bool, str]] = []

    for m in _RANGE_PCT.finditer(text):
        spans.append(
            (m.start(), m.end(), "pct", _f(m.group(2)), _f(m.group(3)),
             bool(m.group(1).strip() or m.group(4)), m.group(0))
        )
    for m in _RANGE_USD.finditer(text):
        # "$100-200M": the suffix on the high end governs both when the low end
        # has none. "$113M-$327M": each carries its own.
        hi_mult = _MULT[m.group(5).lower()] if m.group(5) else 1.0
        lo_mult = _MULT[m.group(3).lower()] if m.group(3) else hi_mult
        spans.append(
            (m.start(), m.end(), "usd", _f(m.group(2)) * lo_mult, _f(m.group(4)) * hi_mult,
             bool(m.group(1).strip() or m.group(6)), m.group(0))
        )

    def _overlaps(a: tuple[int, int], b: tuple[int, int, str, float, float, bool, str]) -> bool:
        return not (a[1] <= b[0] or b[1] <= a[0])

    for m in _ONE_PCT.finditer(text):
        if any(_overlaps((m.start(), m.end()), s) for s in spans):
            continue
        v = _f(m.group(2))
        spans.append((m.start(), m.end(), "pct", v, v,
                      bool(m.group(1).strip() or m.group(3)), m.group(0)))
    for m in _ONE_USD.finditer(text):
        if any(_overlaps((m.start(), m.end()), s) for s in spans):
            continue
        v = _f(m.group(2)) * (_MULT[m.group(3).lower()] if m.group(3) else 1.0)
        spans.append((m.start(), m.end(), "usd", v, v,
                      bool(m.group(1).strip() or m.group(4)), m.group(0)))

    return sorted(spans, key=lambda s: s[0])


# --- period resolution ------------------------------------------------------

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(20\d{2})\b", re.I
)
_QUARTER_RE = re.compile(r"\bQ([1-4])(?:\s*[-–—]\s*Q([1-4]))?\s+(20\d{2})\b", re.I)
_HALF_RE = re.compile(r"\b(early|mid|late|end[- ]of|H1|H2)[- ](20\d{2})\b", re.I)
_YEAR_RE = re.compile(r"\b(?:in|during|for|as of)\s+(20\d{2})\b", re.I)

#: Vague spans, as inclusive month ranges. These are what the prose actually
#: says — "mid-2026", "early 2026" — and each is kept at its own width rather
#: than rounded to a half-year.
#:
#: Half-year buckets were tried first and were wrong in a way worth recording:
#: "January 2026" and "mid-2026" both fall in H1, so the Hyperliquid report's
#: own perfectly consistent "~44% as of mid-2026, up from 36.4% in January 2026"
#: collapsed into a single bucket and read as a self-contradiction. A period
#: scheme that cannot express "then" and "now" inside one sentence turns every
#: growth statement into a defect.
_SPAN_MONTHS: dict[str, tuple[int, int]] = {
    "early": (1, 4), "mid": (5, 8), "late": (9, 12),
    "end-of": (10, 12), "h1": (1, 6), "h2": (7, 12),
}


def _period_key(year: int, lo: int, hi: int, label: str) -> str:
    return f"{year}-{label}" if label else f"{year}-{lo:02d}"


#: period key -> (year, first_month, last_month), for containment tests.
_PERIOD_RANGES: dict[str, tuple[int, int, int]] = {}


def _register(year: int, lo: int, hi: int, label: str = "") -> str:
    key = _period_key(year, lo, hi, label)
    _PERIOD_RANGES[key] = (year, lo, hi)
    return key


def _resolve_period(context: str, fallback: datetime) -> tuple[str, bool]:
    """The period a claim is about, at whatever grain the prose supports.

    Month precision when the prose gives a month, a four-month span for
    "mid-2026", a quarter for "Q3 2025", the whole year for a bare year. Two
    claims are compared only when their periods are equal or one contains the
    other, so "January 2026" and "mid-2026" are correctly never compared while
    "August 2026" and "mid-2026" are.

    ``fallback`` is the report's own date, used when the clause carries no
    temporal qualifier at all — an undated claim is a claim about the moment the
    report was written.

    Returns ``(period, explicit)``. ``explicit`` is False for the fallback, and
    the caller uses it to decide whether two claims are close enough in
    *writing* time to be comparable — see ``MAX_INFERRED_GAP_DAYS``.
    """
    m = _MONTH_RE.search(context)
    if m:
        month = _MONTHS[m.group(1).lower()]
        return _register(int(m.group(2)), month, month), True
    q = _QUARTER_RE.search(context)
    if q:
        first = int(q.group(1))
        last = int(q.group(2)) if q.group(2) else first
        if last < first:
            return "ambiguous", True
        year = int(q.group(3))
        return _register(year, first * 3 - 2, last * 3, f"Q{first}" if first == last else f"Q{first}-Q{last}"), True
    h = _HALF_RE.search(context)
    if h:
        lo, hi = _SPAN_MONTHS.get(h.group(1).lower().replace(" ", "-"), (1, 12))
        return _register(int(h.group(2)), lo, hi, h.group(1).lower().replace(" ", "-")), True
    y = _YEAR_RE.search(context)
    if y:
        return _register(int(y.group(1)), 1, 12, "full"), True
    return _register(fallback.year, fallback.month, fallback.month), False


def _periods_comparable(a: str, b: str) -> bool:
    """Equal, or one span wholly contains the other.

    Containment rather than overlap. "August 2026" sits inside "mid-2026", so
    those are two ways of saying the same thing and must be compared. "January
    2026" and "mid-2026" do not overlap at all. A partial overlap — were the
    vocabulary ever to produce one — is two different periods that happen to
    share months, and comparing across it would be the half-year bug again.
    """
    if a == b:
        return True
    ra, rb = _PERIOD_RANGES.get(a), _PERIOD_RANGES.get(b)
    if not ra or not rb or ra[0] != rb[0]:
        return False
    return (ra[1] <= rb[1] and ra[2] >= rb[2]) or (rb[1] <= ra[1] and rb[2] >= ra[2])


#: Clause boundaries. A date qualifier binds to the number in its own clause;
#: letting it reach across a comma or a parenthesis is what made the GMX
#: report's undated "70-80%+" borrow the "(44% as of Jan 2026)" date sitting
#: next to it, and the two figures then looked like one period's contradiction.
_CLAUSE_BREAK = re.compile(r"[,;()\[\]—–]|\s-\s")


def _period_context(sentence: str, start: int, end: int) -> str:
    """The clause around a value, looking forward first then backward.

    Forward first because English puts the qualifier after the figure: "~44% of
    on-chain perp volume as of mid-2026", "36.4% in January 2026".
    """
    fwd = sentence[end:]
    brk = _CLAUSE_BREAK.search(fwd)
    forward = fwd[: brk.start()] if brk else fwd
    if _MONTH_RE.search(forward) or _QUARTER_RE.search(forward) or _HALF_RE.search(forward) or _YEAR_RE.search(forward):
        return forward
    back = sentence[:start]
    breaks = list(_CLAUSE_BREAK.finditer(back))
    return back[breaks[-1].end():] if breaks else back


# --- entity resolution ------------------------------------------------------


def _build_alias_map(extra: Iterable[tuple[str, str]] = ()) -> dict[str, str]:
    """alias (lowercased) -> canonical entity name.

    ``extra`` supplies ``(alias, canonical)`` pairs from the ``projects`` table.
    Mapping a ticker to itself rather than to its project would split one entity
    in two — "XPL" and "Plasma" would accumulate separate claim sets and neither
    would ever be compared against the other.
    """
    aliases = dict(SEED_ENTITY_ALIASES)
    for alias, canonical in extra:
        if alias and alias.strip() and canonical and canonical.strip():
            aliases.setdefault(alias.strip().lower(), canonical.strip())
    return aliases


def _entity_mentions(text: str, aliases: dict[str, str]) -> list[tuple[int, int, str]]:
    """Every resolvable entity mention in ``text``, as (start, end, canonical).

    Ambiguous ticker-shaped aliases ("LINK", "HYPE", "Drift") are only accepted
    in upper case or when they are the exact cased alias, so the English word
    "drift" in "liquidity drift" does not become an entity.
    """
    out: list[tuple[int, int, str]] = []
    for alias, canonical in aliases.items():
        flags = 0 if alias in _AMBIGUOUS_ALIASES else re.I
        pattern = re.compile(rf"\b{re.escape(alias)}\b", flags)
        probe = alias.upper() if alias in _AMBIGUOUS_ALIASES else alias
        for m in re.finditer(rf"\b{re.escape(probe)}\b", text, flags):
            out.append((m.start(), m.end(), canonical))
        if alias in _AMBIGUOUS_ALIASES and probe != canonical:
            for m in re.finditer(rf"\b{re.escape(canonical)}\b", text):
                out.append((m.start(), m.end(), canonical))
        del pattern
    return sorted(set(out))


def _attribute_entity(
    pos: int, mentions: Sequence[tuple[int, int, str]]
) -> str | None:
    """Nearest entity mention *preceding* ``pos`` within the attribution window.

    Preceding-only and sentence-scoped (the caller passes one sentence at a
    time). English financial prose puts the subject before its number —
    "Hyperliquid commands 70-80%" — and the possessive form "versus GMX's ~$2.8B
    30-day volume" puts it there too. Looking forward as well would attribute
    "GMX's ~$2.8B ... below Hyperliquid" to Hyperliquid.

    Returns None when nothing resolves, and the caller drops the claim. Falling
    back to the report's own project would be the single most damaging thing
    this module could do: it would relabel every third-party claim as a claim
    about the report subject, which is exactly the blindness that let the
    Hyperliquid/GMX contradiction through.
    """
    best: tuple[int, str] | None = None
    for start, end, canonical in mentions:
        if end <= pos and pos - end <= _ATTRIBUTION_WINDOW:
            if best is None or end > best[0]:
                best = (end, canonical)
    return best[1] if best else None


def _classify_metric(sentence: str, value_start: int, value_end: int, unit: str) -> str | None:
    """Canonical metric key for the value at ``[value_start, value_end)``.

    Three conditions, all necessary:

    * the metric phrase agrees with the value's unit — "$6.66B TVL" is a usd
      metric, "44% share" a pct one, and a percentage beside the word TVL is not
      a TVL claim;
    * it sits within ``_METRIC_WINDOW`` characters of the value;
    * **no other digit lies between them.** This is what defeats the
      percentage-of-a-metric construction that dominates this corpus:
      "(~$589M, ~4.5% of float / ~3.2% of MCap)" has MCap close to $589M, but
      two numbers stand in the gap, so the unlock size is not read as a market
      cap. Without this rule the audit invented five different market caps for
      Hyperliquid in a single report.
    """
    best: tuple[int, str] | None = None
    for key, metric_unit, regex in _METRIC_RES:
        if metric_unit != unit:
            continue
        for m in regex.finditer(sentence):
            if m.end() <= value_start:
                distance, gap = value_start - m.end(), sentence[m.end():value_start]
            elif m.start() >= value_end:
                distance, gap = m.start() - value_end, sentence[value_end:m.start()]
            else:
                continue  # overlapping: the number is inside the metric phrase
            if distance > _METRIC_WINDOW or _ANY_NUMBER.search(gap):
                continue
            if best is None or distance < best[0]:
                best = (distance, key)
    return best[1] if best else None


def extract_claims(
    text: str,
    *,
    evaluation_id: str,
    report_project: str,
    section: str,
    report_date: datetime,
    aliases: dict[str, str] | None = None,
) -> list[Claim]:
    """Pull ``(entity, metric, value, period)`` tuples out of one prose section.

    Conservative by construction. A number survives only if all four of these
    resolve: a known entity before it in the same sentence, a metric phrase near
    it with a matching unit, a parseable value, and a period. Anything else is
    discarded silently. The corpus contains 842 numeric tokens and the vast
    majority of them are not comparable facts about a named entity — a sweep
    that flagged them all would be noise, so the extractor's job is as much
    about what it refuses as what it returns.
    """
    aliases = aliases or _build_alias_map()
    claims: list[Claim] = []

    for sentence in _SENTENCE_SPLIT.split(text or ""):
        sentence = sentence.strip()
        if not sentence or len(sentence) > 4000:
            continue
        mentions = _entity_mentions(sentence, aliases)
        if not mentions:
            continue
        here: list[Claim] = []
        #: Last metric classified for each unit in this sentence, for the
        #: continuation rule below.
        last_metric: dict[str, str] = {}
        for start, end, unit, lo, hi, hedged, raw in _find_values(sentence):
            entity = _attribute_entity(start, mentions)
            if entity is None:
                continue
            metric = _classify_metric(sentence, start, end, unit)
            if metric is None and _CONTINUATION.search(sentence[:start]):
                metric = last_metric.get(unit)
            if metric is None:
                continue
            last_metric[unit] = metric
            # The period qualifier can sit either side of the number, but must
            # stay inside the clause: a parenthetical "(44% as of Jan 2026)"
            # binds its own date and must not lend it to its neighbours.
            period, explicit = _resolve_period(
                _period_context(sentence, start, end), report_date
            )
            if period == "ambiguous":
                continue
            here.append(
                Claim(
                    entity=entity,
                    metric=metric,
                    unit=unit,
                    lo=lo,
                    hi=hi,
                    hedged=hedged,
                    period=period,
                    evaluation_id=evaluation_id,
                    report_project=report_project,
                    section=section,
                    quote=sentence[:400],
                    raw=raw.strip(),
                    period_explicit=explicit,
                    report_date=report_date.date().isoformat(),
                )
            )
        claims.extend(_drop_comparatives(here))
    return claims


def _drop_comparatives(claims: list[Claim]) -> list[Claim]:
    """Discard claims from a sentence that contradicts itself about one metric.

    "…processes ~20x GMX's daily volume (~$30B vs ~$100-200M)" is a comparison:
    the $30B is Hyperliquid's and the $100-200M is GMX's, but the nearest
    preceding entity to both is GMX. Nearest-preceding attribution cannot
    resolve an X-versus-Y construction, and a rule that tried to would be
    guessing.

    The reliable signal is that the mistake announces itself — one sentence
    asserting two incompatible values for the same entity and metric and period
    is a parse failure, not a finding. Drop the whole group rather than pick a
    winner. Costs recall on comparative sentences; buys the guarantee that a
    misattributed comparator never reaches the ledger.
    """
    bad: set[int] = set()
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if (a.entity, a.metric) != (b.entity, b.metric):
                continue
            if not _periods_comparable(a.period, b.period):
                continue
            if _disjoint(a, b):
                bad.add(id(a))
                bad.add(id(b))
    return [c for c in claims if id(c) not in bad]


# ---------------------------------------------------------------------------
# Layer 2 — deterministic candidate conflict detection
# ---------------------------------------------------------------------------


@dataclass
class Conflict:
    """Two or more claims about one (entity, metric, period) that cannot all hold."""

    entity: str
    metric: str
    period: str
    unit: str
    claims: list[Claim]
    spread_pct: float
    #: True when the same number appears against two different periods — the
    #: Hyperliquid 44% shape. Called out separately because it is invisible to
    #: a value-only comparison and is the form most likely to become a bad
    #: decision rule.
    date_attribution: bool = False
    note: str = ""

    def fingerprint(self) -> str:
        return fingerprint_of(self)

    @property
    def severity(self) -> str:
        if self.date_attribution:
            return "high"
        if self.spread_pct >= 50:
            return "high"
        if self.spread_pct >= 20:
            return "medium"
        return "low"

    def to_json(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "metric": self.metric,
            "period": self.period,
            "unit": self.unit,
            "spread_pct": round(self.spread_pct, 1),
            "date_attribution": self.date_attribution,
            "severity": self.severity,
            "note": self.note,
            "claims": [c.to_json() for c in self.claims],
        }


def fingerprint_of(conflict: Conflict) -> str:
    """Stable identity for a conflict, across audit runs.

    Built from the entity, the metric, the period and the *set of source claims*
    — each claim identified by its evaluation, its section and its literal
    value. Re-running the audit over an unchanged corpus reproduces the same
    hash byte for byte, which is what makes the sweep idempotent. A new report
    that joins the argument changes the claim set and therefore produces a new
    fingerprint, which is correct: it is a different finding.
    """
    parts = sorted(
        f"{c.evaluation_id}|{c.section}|{c.lo}|{c.hi}" for c in conflict.claims
    )
    payload = "||".join([conflict.entity, conflict.metric, conflict.period, *parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _disjoint(a: Claim, b: Claim) -> bool:
    lo_a, hi_a = a.interval
    lo_b, hi_b = b.interval
    return hi_a < lo_b or hi_b < lo_a


def _comparable(a: Claim, b: Claim) -> bool:
    """Whether two claims are even eligible to contradict each other.

    Never within one evaluation — intra-run drift is ``agents/reconciliation``'s
    job and flagging it here would double-report it.

    And when *both* periods were inferred from the report date rather than
    stated, the reports must have been written within ``MAX_INFERRED_GAP_DAYS``.
    Two undated figures written two months apart are not a contradiction; they
    are a time series.
    """
    if a.evaluation_id == b.evaluation_id:
        return False
    if a.period_explicit or b.period_explicit:
        return True
    if not (a.report_date and b.report_date):
        return True
    gap = abs(
        (datetime.fromisoformat(a.report_date) - datetime.fromisoformat(b.report_date)).days
    )
    return gap <= MAX_INFERRED_GAP_DAYS


def _spread(claims: Sequence[Claim]) -> float:
    mids = [(c.lo + c.hi) / 2 for c in claims]
    lo, hi = min(mids), max(mids)
    return 0.0 if lo == 0 else abs(hi - lo) / abs(lo) * 100.0


def detect_conflicts(claims: Sequence[Claim]) -> list[Conflict]:
    """Group claims and return the buckets that cannot all be true.

    Two rules define a conflict:

    * **Value conflict** — same entity, same metric, same period, and two claims
      whose hedged intervals do not overlap. Different-period claims are never
      compared, so a metric that legitimately moved between April and August
      does not register.
    * **Date-attribution conflict** — same entity, same metric, *different*
      periods, and the same value asserted for both. This is the Hyperliquid
      44% shape: nothing about either number is individually wrong, and a
      value-only comparison sees nothing, but one of the two dates must be.

    Only claims from **different evaluations** are compared. Intra-run
    disagreement belongs to ``agents/reconciliation``.
    """
    conflicts: list[Conflict] = []

    buckets: dict[tuple[str, str], list[Claim]] = {}
    for c in claims:
        buckets.setdefault((c.entity, c.metric), []).append(c)

    for (entity, metric), group in sorted(buckets.items()):
        if len({c.evaluation_id for c in group}) < 2:
            continue
        # Periods are spans of differing width, so a claim can be comparable
        # with two claims that are not comparable with each other. Cluster on
        # the "clashes with" relation rather than on an exact period key.
        clusters: list[list[Claim]] = []
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if not _comparable(a, b) or not _periods_comparable(a.period, b.period):
                    continue
                if not _disjoint(a, b):
                    continue
                joined = [cl for cl in clusters if a in cl or b in cl]
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

        for clashing in clusters:
            periods = sorted({c.period for c in clashing})
            period = periods[0] if len(periods) == 1 else " / ".join(periods)
            conflicts.append(
                Conflict(
                    entity=entity,
                    metric=metric,
                    period=period,
                    unit=clashing[0].unit,
                    claims=clashing,
                    spread_pct=_spread(clashing),
                    note=(
                        f"{len({c.evaluation_id for c in clashing})} evaluations state "
                        f"non-overlapping values for {entity} {metric} in {period}."
                    ),
                )
            )

    # Date-attribution: the same value pinned to two different periods by two
    # different evaluations.
    by_metric: dict[tuple[str, str], list[Claim]] = {}
    for c in claims:
        by_metric.setdefault((c.entity, c.metric), []).append(c)

    for (entity, metric), group in sorted(by_metric.items()):
        seen: dict[tuple[float, float], list[Claim]] = {}
        for c in group:
            seen.setdefault((c.lo, c.hi), []).append(c)
        for value, same_value in seen.items():
            periods = {c.period for c in same_value}
            evals = {c.evaluation_id for c in same_value}
            if len(periods) < 2 or len(evals) < 2:
                continue
            # At least one of the disagreeing datings has to be something the
            # prose actually asserted. If every period was inferred from a
            # report date, then "different periods" only means "written months
            # apart", and a figure that happens to recur is a stable metric, not
            # a misdated one.
            if not any(c.period_explicit for c in same_value):
                continue
            # The two datings must be genuinely different, not two widths of the
            # same window: "August 2026" inside "mid-2026" is one dating stated
            # twice. And they must come from different evaluations, or this is
            # one report restating its own figure.
            if not any(
                a.evaluation_id != b.evaluation_id
                and not _periods_comparable(a.period, b.period)
                for i, a in enumerate(same_value)
                for b in same_value[i + 1:]
            ):
                continue
            conflicts.append(
                Conflict(
                    entity=entity,
                    metric=metric,
                    period=" vs ".join(sorted(periods)),
                    unit=same_value[0].unit,
                    claims=same_value,
                    spread_pct=0.0,
                    date_attribution=True,
                    note=(
                        f"The same figure ({same_value[0].raw}) is dated to "
                        f"{' and '.join(sorted(periods))} by different evaluations. "
                        "At most one dating can be right; a signpost built on "
                        "either cannot fire correctly."
                    ),
                )
            )

    return conflicts


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

#: One evaluation must contribute its prose exactly once. ``reports.content ->
#: draft_report`` for the newer rows is a verbatim copy of the same evaluation's
#: ``report_writer`` output, so reading both would compare a report against
#: itself and manufacture a "contradiction" from a duplicate. ``reports`` wins
#: where it exists — it is the newer, canonical store — and ``agent_outputs`` is
#: the fallback and the only path for anything before 2026-08-25.
_CORPUS_SQL = """
WITH from_reports AS (
    SELECT r.evaluation_id,
           COALESCE(p.name, 'unknown')            AS project_name,
           r.created_at,
           COALESCE(r.content -> 'draft_report' -> 'sections',
                    r.content -> 'sections')      AS sections,
           'reports'::text                        AS origin
    FROM reports r
    LEFT JOIN evaluations e ON e.id = r.evaluation_id
    LEFT JOIN projects p ON p.id = e.project_id
),
from_outputs AS (
    SELECT ao.evaluation_id,
           COALESCE(p.name, 'unknown')            AS project_name,
           ao.created_at,
           ao.output -> 'sections'                AS sections,
           'agent_outputs'::text                  AS origin
    FROM agent_outputs ao
    LEFT JOIN evaluations e ON e.id = ao.evaluation_id
    LEFT JOIN projects p ON p.id = e.project_id
    WHERE ao.agent_name = 'report_writer'
      AND ao.evaluation_id NOT IN (SELECT evaluation_id FROM from_reports
                                   WHERE sections IS NOT NULL)
)
SELECT * FROM from_reports WHERE sections IS NOT NULL
UNION ALL
SELECT * FROM from_outputs WHERE sections IS NOT NULL
ORDER BY created_at
"""


async def load_corpus() -> list[dict[str, Any]]:
    """Every report's 24 prose sections, one row per evaluation. Read-only."""
    async with async_session() as session:
        result = await session.execute(sql_text(_CORPUS_SQL))
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def _project_names() -> list[tuple[str, str]]:
    """``(alias, canonical)`` pairs from the projects table.

    Ticker and coingecko id both resolve to the project's ``name``, so "XPL",
    "plasma-xpl" and "Plasma" are one entity rather than three.
    """
    async with async_session() as session:
        result = await session.execute(sql_text("SELECT name, ticker, coingecko_id FROM projects"))
        out: list[tuple[str, str]] = []
        for row in result.mappings().all():
            canonical = str(row["name"] or "").strip()
            if not canonical:
                continue
            for value in (row["name"], row["ticker"], row["coingecko_id"]):
                if value:
                    out.append((str(value), canonical))
    return out


def claims_from_corpus(rows: Sequence[dict[str, Any]], aliases: dict[str, str]) -> list[Claim]:
    claims: list[Claim] = []
    for row in rows:
        sections = row.get("sections") or {}
        if isinstance(sections, str):
            try:
                sections = json.loads(sections)
            except ValueError:
                continue
        if not isinstance(sections, dict):
            continue
        created = row.get("created_at") or datetime.now(timezone.utc)
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        for section, body in sorted(sections.items()):
            text = body if isinstance(body, str) else json.dumps(body, default=str)
            claims.extend(
                extract_claims(
                    text,
                    evaluation_id=str(row["evaluation_id"]),
                    report_project=str(row.get("project_name") or "unknown"),
                    section=str(section),
                    report_date=created,
                    aliases=aliases,
                )
            )
    return claims


# ---------------------------------------------------------------------------
# Layer 3 — check against an authoritative source
# ---------------------------------------------------------------------------


@dataclass
class Verification:
    """One observation of the ground truth for a conflicted metric."""

    at: str
    source: str
    ok: bool
    value: float | None = None
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


async def verify_candidate(conflict: Conflict) -> Verification:
    """Observe the authoritative value for ``conflict`` right now.

    DeFiLlama and CoinGecko are already registered tools and need no Anthropic
    key, so this step runs today. Three outcomes are kept distinct and are never
    collapsed (CONTRACTS §2.7): a value was found; the source has no answer for
    this metric; the fetch failed. Recording "no answer" as "fetch failed" would
    make a permanently unverifiable finding look like a flaky one and it would be
    retried forever.
    """
    now = datetime.now(timezone.utc).isoformat()
    spec = CANONICAL_METRICS.get(conflict.metric, {})
    verifier = spec.get("verifier", "")

    # A share-of-market figure for a past half-year is not served by either
    # source. Say so explicitly rather than failing: this is the Hyperliquid
    # 44%-on-two-dates case, and it is permanently unverifiable by spot query.
    if conflict.date_attribution:
        return Verification(
            at=now,
            source="none",
            ok=False,
            detail=(
                "Date-attribution conflict: no spot source can adjudicate which "
                "of two past dates a figure belongs to. The contradiction is "
                "nonetheless certain — at most one dating is correct."
            ),
        )

    try:
        if verifier.startswith("defillama"):
            return await _verify_defillama(conflict, verifier, now)
        if verifier.startswith("coingecko"):
            return await _verify_coingecko(conflict, verifier, now)
    except Exception as exc:  # network, parse, anything
        logger.warning("Consistency verification failed for %s: %s", conflict.metric, exc)
        return Verification(at=now, source=verifier or "unknown", ok=False,
                            detail=f"fetch failed: {exc}")

    return Verification(at=now, source="none", ok=False,
                        detail=f"no verifier configured for metric {conflict.metric}")


async def _verify_defillama(conflict: Conflict, verifier: str, now: str) -> Verification:
    import httpx

    from app.tools.defillama import BASE_URL

    slug = conflict.entity.lower().replace(" ", "-")
    async with httpx.AsyncClient(timeout=20.0) as client:
        if verifier == "defillama_tvl":
            resp = await client.get(f"{BASE_URL}/protocol/{slug}")
            if resp.status_code != 200:
                return Verification(at=now, source="defillama", ok=False,
                                    detail=f"HTTP {resp.status_code} for {slug}")
            data = resp.json()
            tvl = data.get("currentChainTvls", {})
            total = sum(v for k, v in tvl.items() if isinstance(v, (int, float)) and "-" not in k)
            return Verification(at=now, source="defillama", ok=bool(total), value=total or None,
                                detail=f"protocol/{slug} currentChainTvls")
        # Perp share and perp volume both need /overview/derivatives, which as
        # of 2026-08-26 answers HTTP 402 on the free plan (verified by direct
        # probe). Report that as a distinct, non-retryable outcome: a 402 is a
        # permanent "this account cannot see this", not a transient failure, and
        # a re-check loop that treats it as transient will hammer it forever.
        #
        # Deliberately NOT substituting the free /overview/dexs figures. That
        # endpoint returns 200 and carries "Hyperliquid Spot Orderbook" — spot
        # volume, not perpetuals. Filling a perps field with a spot number would
        # be worse than an empty one: it would adjudicate a real contradiction
        # with the wrong quantity and write a confident correction from it.
        resp = await client.get("https://api.llama.fi/overview/derivatives")
        if resp.status_code == 402:
            return Verification(
                at=now, source="defillama", ok=False,
                detail=("DeFiLlama /overview/derivatives requires the paid plan (HTTP 402). "
                        "The free /overview/dexs endpoint covers spot only and is not a "
                        "substitute for a perpetuals figure. Permanently unverifiable "
                        "from this source — do not retry."),
            )
        if resp.status_code != 200:
            return Verification(at=now, source="defillama", ok=False,
                                detail=f"HTTP {resp.status_code} for overview/derivatives")
        data = resp.json()
        protocols = data.get("protocols") or []
        me = next((p for p in protocols if str(p.get("name", "")).lower() == conflict.entity.lower()), None)
        if me is None:
            return Verification(at=now, source="defillama", ok=False,
                                detail=f"{conflict.entity} absent from derivatives overview")
        if verifier == "defillama_perp_share":
            total = sum(p.get("total24h") or 0 for p in protocols)
            share = (me.get("total24h") or 0) / total * 100 if total else None
            return Verification(at=now, source="defillama", ok=share is not None, value=share,
                                detail="overview/derivatives total24h share")
        if verifier == "defillama_volume_30d":
            value = me.get("total30d")
            return Verification(at=now, source="defillama", ok=value is not None, value=value,
                                detail="overview/derivatives total30d")
        value = me.get("total24h")
        return Verification(at=now, source="defillama", ok=value is not None, value=value,
                            detail="overview/derivatives total24h")


async def _verify_coingecko(conflict: Conflict, verifier: str, now: str) -> Verification:
    # Reuse the tools-layer fetcher: it applies the API-key header, backs off on
    # HTTP 429, and — critically — recognises the free tier's HTTP 200 carrying
    # a body-level 429 (CONTRACTS §2.7). A bare httpx.get here would silently
    # record a rate limit as "the metric does not exist".
    import httpx

    from app.tools.coingecko import _get_with_backoff, _is_body_rate_limit

    coin_id = conflict.entity.lower().replace(" ", "-")
    async with httpx.AsyncClient(timeout=20.0) as client:
        # retry_body_429=True: this call has no outer ladder of its own, so the
        # body-level 429 must be retried here or it is parsed as data.
        resp = await _get_with_backoff(
            client,
            f"coins/{coin_id}",
            params={
                "localization": "false", "tickers": "false",
                "community_data": "false", "developer_data": "false",
            },
            retry_body_429=True,
        )
    if resp is None:
        return Verification(at=now, source="coingecko", ok=False, detail="fetch failed")
    if _is_body_rate_limit(resp):
        return Verification(at=now, source="coingecko", ok=False,
                            detail="rate limited (HTTP 200 body-level 429) — not a missing value")
    data = resp.json()
    market = data.get("market_data") or {}
    key = "market_cap" if verifier == "coingecko_market_cap" else "fully_diluted_valuation"
    value = (market.get(key) or {}).get("usd")
    return Verification(at=now, source="coingecko", ok=value is not None, value=value,
                        detail=f"coins/{coin_id} market_data.{key}.usd")


def classify_recheck(
    first: Verification, second: Verification, conflict: Conflict
) -> tuple[str, str]:
    """Transient vs persistent, from two observations of the ground truth.

    Returns ``(status, rationale)``. See the module docstring — the whole point
    of the second check is that it measures the *source's* volatility, not the
    reports, which cannot have changed.
    """
    if not (first.ok and second.ok) or first.value is None or second.value is None:
        return (
            "unverified",
            "No authoritative reading available at one or both checks. The "
            "contradiction stands on its own — two reports cannot both be right "
            "— but which one is wrong is undetermined. "
            + (second.detail or first.detail),
        )

    base = abs(first.value) or 1.0
    movement = abs(second.value - first.value) / base * 100.0

    if movement > GROUND_TRUTH_MOVEMENT_PCT:
        return (
            "transient",
            f"The authoritative value moved {movement:.1f}% in "
            f"{RECHECK_INTERVAL_HOURS}h ({first.value:,.4g} -> {second.value:,.4g}). "
            "A metric this volatile can differ legitimately between two reports; "
            "the defect is undated figures, not a wrong figure.",
        )

    # Stable ground truth. Which claims does it contradict?
    truth = second.value
    wrong = [c for c in conflict.claims if not (c.interval[0] <= truth <= c.interval[1])]
    right = [c for c in conflict.claims if c not in wrong]
    if not wrong:
        return (
            "resolved",
            f"Authoritative value {truth:,.4g} is stable and consistent with every "
            "claim once hedging is allowed for. No correction needed.",
        )
    naming = ", ".join(sorted({f"{c.report_project} ({c.section})" for c in wrong}))
    return (
        "confirmed_error",
        f"The authoritative value is stable at {truth:,.4g} (moved {movement:.1f}% in "
        f"{RECHECK_INTERVAL_HOURS}h), so movement cannot explain the disagreement. "
        f"Incompatible claim(s): {naming}."
        + (f" Consistent with: {', '.join(sorted({c.report_project for c in right}))}." if right else ""),
    )


# ---------------------------------------------------------------------------
# Layer 4 — the append-only findings ledger
# ---------------------------------------------------------------------------

Status = Literal["open", "transient", "confirmed_error", "resolved", "unverified", "superseded"]


_INSERT_REVISION = """
INSERT INTO consistency_findings
    (id, fingerprint, revision, supersedes_id, audit_run_id, entity, metric,
     as_of_period, severity, status, spread_pct, date_attribution, claims,
     verifications, rationale, warning_text, first_observed_at, last_checked_at)
VALUES
    (:id, :fingerprint, :revision, :supersedes_id, :audit_run_id, :entity, :metric,
     :as_of_period, :severity, :status, :spread_pct, :date_attribution,
     CAST(:claims AS jsonb), CAST(:verifications AS jsonb), :rationale,
     :warning_text, :first_observed_at, :last_checked_at)
ON CONFLICT (fingerprint, revision) DO NOTHING
RETURNING id
"""

#: Current state of every finding: the highest revision of each fingerprint.
_CURRENT_SQL = """
SELECT DISTINCT ON (fingerprint) *
FROM consistency_findings
ORDER BY fingerprint, revision DESC
"""


async def _next_revision(session: Any, fingerprint: str) -> tuple[int, datetime | None]:
    row = (
        await session.execute(
            sql_text(
                "SELECT MAX(revision) AS r, MIN(first_observed_at) AS f "
                "FROM consistency_findings WHERE fingerprint = :fp"
            ),
            {"fp": fingerprint},
        )
    ).mappings().first()
    if not row or row["r"] is None:
        return 1, None
    return int(row["r"]) + 1, row["f"]


async def record_finding(
    conflict: Conflict,
    *,
    audit_run_id: str,
    verification: Verification | None = None,
    status: Status = "open",
    rationale: str = "",
) -> dict[str, Any]:
    """Append the first revision of a finding. Idempotent.

    Re-running the audit over an unchanged corpus recomputes the same
    fingerprint and, because revision 1 already exists, inserts nothing. The
    ``ON CONFLICT (fingerprint, revision) DO NOTHING`` is the guarantee; the
    caller does not have to remember to check first.
    """
    fp = conflict.fingerprint()
    now = datetime.now(timezone.utc)
    warning = _render_one(conflict, status, rationale)

    async with async_session() as session:
        revision, first_seen = await _next_revision(session, fp)
        if revision > 1:
            # Already known. Do not append a duplicate observation — an audit
            # sweep that re-detects an unchanged conflict has learned nothing.
            return {"fingerprint": fp, "inserted": False, "revision": revision - 1}
        result = await session.execute(
            sql_text(_INSERT_REVISION),
            {
                "id": str(uuid.uuid4()),
                "fingerprint": fp,
                "revision": 1,
                "supersedes_id": None,
                "audit_run_id": audit_run_id,
                "entity": conflict.entity,
                "metric": conflict.metric,
                "as_of_period": conflict.period,
                "severity": conflict.severity,
                "status": status,
                "spread_pct": round(conflict.spread_pct, 2),
                "date_attribution": conflict.date_attribution,
                "claims": json.dumps([c.to_json() for c in conflict.claims], default=str),
                "verifications": json.dumps(
                    [verification.to_json()] if verification else [], default=str
                ),
                "rationale": rationale or conflict.note,
                "warning_text": warning,
                "first_observed_at": first_seen or now,
                "last_checked_at": now if verification else None,
            },
        )
        inserted = result.first() is not None
        await session.commit()
    return {"fingerprint": fp, "inserted": inserted, "revision": 1}


async def recheck_finding(fingerprint: str) -> dict[str, Any]:
    """The second check. Appends a revision; never mutates the first.

    Refuses to run before ``RECHECK_INTERVAL_HOURS`` have passed. Two readings
    taken a minute apart cannot distinguish a volatile metric from a stable one,
    so an early re-check would answer "stable" for everything and every wrong
    report would be graded a confirmed error. The interval is the measurement.
    """
    async with async_session() as session:
        row = (
            await session.execute(
                sql_text(
                    "SELECT * FROM consistency_findings WHERE fingerprint = :fp "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"fp": fingerprint},
            )
        ).mappings().first()
    if not row:
        return {"error": f"unknown finding {fingerprint}"}

    verifications = list(row["verifications"] or [])
    first_at = datetime.fromisoformat(verifications[0]["at"]) if verifications else None
    now = datetime.now(timezone.utc)
    if first_at and now - first_at < timedelta(hours=RECHECK_INTERVAL_HOURS):
        due = first_at + timedelta(hours=RECHECK_INTERVAL_HOURS)
        return {
            "fingerprint": fingerprint,
            "rechecked": False,
            "reason": "interval not elapsed",
            "due_at": due.isoformat(),
        }

    conflict = _conflict_from_row(row)
    second = await verify_candidate(conflict)
    first = (
        Verification(**{k: v for k, v in verifications[0].items() if k in Verification.__annotations__})
        if verifications
        else Verification(at=now.isoformat(), source="none", ok=False, detail="no first check")
    )
    status, rationale = classify_recheck(first, second, conflict)
    return await _append_revision(
        row, status=status, rationale=rationale,
        verifications=verifications + [second.to_json()], supersedes_id=None,
    )


async def supersede_finding(
    fingerprint: str, *, correction: str, status: Status = "confirmed_error"
) -> dict[str, Any]:
    """Record a correction as a NEW revision that supersedes the previous one.

    "Correct them if needed" cannot mean editing the report. CONTRACTS §2.5:
    past reports are the audit record, and for the 18 June cohort they are the
    only surviving copy of the reasoning. Rewriting one would destroy the very
    evidence that this audit exists to reason about, and would make the corpus
    unable to show that the committee ever held the wrong belief — which is the
    thing calibration needs to see.

    So a correction is an append. The wrong claim stays exactly where it was,
    and a later, higher-revision row states what is true and points back at what
    it replaces.
    """
    async with async_session() as session:
        row = (
            await session.execute(
                sql_text(
                    "SELECT * FROM consistency_findings WHERE fingerprint = :fp "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"fp": fingerprint},
            )
        ).mappings().first()
    if not row:
        return {"error": f"unknown finding {fingerprint}"}
    return await _append_revision(
        row, status=status, rationale=correction,
        verifications=list(row["verifications"] or []), supersedes_id=str(row["id"]),
    )


async def _append_revision(
    row: Any, *, status: str, rationale: str,
    verifications: list[dict[str, Any]], supersedes_id: str | None,
) -> dict[str, Any]:
    conflict = _conflict_from_row(row)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        revision = int(row["revision"]) + 1
        result = await session.execute(
            sql_text(_INSERT_REVISION),
            {
                "id": str(uuid.uuid4()),
                "fingerprint": row["fingerprint"],
                "revision": revision,
                "supersedes_id": supersedes_id,
                "audit_run_id": row["audit_run_id"],
                "entity": row["entity"],
                "metric": row["metric"],
                "as_of_period": row["as_of_period"],
                "severity": _severity_after(row["severity"], status),
                "status": status,
                "spread_pct": row["spread_pct"],
                "date_attribution": row["date_attribution"],
                "claims": json.dumps(row["claims"], default=str),
                "verifications": json.dumps(verifications, default=str),
                "rationale": rationale,
                "warning_text": _render_one(conflict, status, rationale),
                "first_observed_at": row["first_observed_at"],
                "last_checked_at": now,
            },
        )
        inserted = result.first() is not None
        await session.commit()
    return {
        "fingerprint": row["fingerprint"], "revision": revision,
        "inserted": inserted, "status": status, "rationale": rationale,
    }


def _severity_after(current: str, status: str) -> str:
    """A transient finding is worth less attention; a confirmed error is not."""
    if status == "transient":
        return "low"
    if status == "resolved":
        return "low"
    if status == "confirmed_error":
        return "high"
    return current


def _conflict_from_row(row: Any) -> Conflict:
    claims = [
        Claim(**{k: v for k, v in c.items() if k in Claim.__dataclass_fields__})
        for c in (row["claims"] or [])
    ]
    return Conflict(
        entity=row["entity"], metric=row["metric"], period=row["as_of_period"],
        unit=claims[0].unit if claims else "pct", claims=claims,
        spread_pct=float(row["spread_pct"] or 0), date_attribution=bool(row["date_attribution"]),
        note=row["rationale"] or "",
    )


async def active_findings(limit: int = 50) -> list[dict[str, Any]]:
    """Current revision of every finding that is not resolved, worst first."""
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                f"SELECT * FROM ({_CURRENT_SQL}) cur "
                "WHERE status <> 'resolved' "
                "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "        first_observed_at DESC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Layer 5 — the warning agents actually read
# ---------------------------------------------------------------------------


def _fmt(unit: str, lo: float, hi: float, raw: str) -> str:
    return raw if raw else (f"{lo:g}-{hi:g}%" if unit == "pct" else f"${lo:,.0f}")


def _render_one(conflict: Conflict, status: str, rationale: str) -> str:
    label = CANONICAL_METRICS.get(conflict.metric, {}).get("label", conflict.metric)
    lines = [f"- {conflict.entity} — {label} ({conflict.period}) [{status}]"]
    for c in conflict.claims:
        lines.append(
            f"    {c.report_project} report, {c.section}: {_fmt(c.unit, c.lo, c.hi, c.raw)}"
        )
    if rationale:
        lines.append(f"    -> {rationale}")
    return "\n".join(lines)


async def render_active_warnings(char_budget: int = WARNING_CHAR_BUDGET) -> str:
    """The block an agent sees. Bounded, deterministic, worst-first.

    Returns "" when there is nothing to say, so the cost is exactly zero on a
    clean corpus — the common case — and the caller can splice it in
    unconditionally.

    **Where this belongs, and why.** Three homes were weighed:

    * ``backend/app/memory/*.md`` — loaded unconditionally into every agent's
      prompt by ``get_agent_context``, and it sits *above*
      ``SYSTEM_PROMPT_VOLATILE_HEADING``, so it is inside the cached prefix and
      costs cache-read rates rather than full input. Reliable. Rejected anyway:
      those files are the fund's constitution — mandate, risk policy, thesis —
      and they have no supersession mechanism. Findings expire, get corrected,
      and get superseded; a constitution that accumulates dated errata is a
      constitution nobody reads. It also grows without bound, and the growth is
      paid by all fifteen agents on every run forever.
    * ``knowledge_chunks`` via ``semantic_search_notes`` — reachable by every
      agent today with no code change at all, since it is in ``_base_tools``.
      Rejected: retrieval is probabilistic, and worse, an agent has no reason to
      *ask*. It does not know a contradiction exists, so it will not query for
      one. The tool's own description also scopes its corpus to "the Learnings
      database" and says project evaluations are not indexed; injecting audit
      findings there would make that description false, in a file this branch
      does not own. A warning that might surface, to an agent that will not ask
      for it, is decoration.
    * **A table plus this bounded render — chosen.** ``consistency_findings`` is
      the system of record: queryable, versioned, joins to evaluations, and
      append-only so the audit trail is itself auditable. This function turns
      the current revisions into a hard-capped block for the prompt. Bounded is
      the load-bearing word: ``WARNING_CHAR_BUDGET`` caps what any run can pay,
      and worst-first ordering means the cap truncates the least important
      finding rather than an arbitrary one.

    The one-line wiring belongs in ``agents/orchestrator.py``, which this branch
    does not own — the block should ride in ``case_context`` beside
    ``canonical_metrics``, where ``BaseAgent.get_system_prompt`` already renders
    it for every agent. See the API endpoint ``GET /api/consistency/warnings``
    for the value to splice in, and the cost note below.
    """
    rows = await active_findings(limit=MAX_RENDERED_WARNINGS * 3)
    if not rows:
        return ""

    header = (
        "=== KNOWN CROSS-REPORT DATA CONTRADICTIONS ===\n"
        "Prior committee reports disagree with each other on the figures below. "
        "Do not treat any of them as settled. If your analysis depends on one, "
        "re-derive it from a primary source and state the date it is as of.\n"
    )
    body: list[str] = []
    used = len(header)
    for row in rows[:MAX_RENDERED_WARNINGS]:
        chunk = row["warning_text"] or ""
        if used + len(chunk) + 1 > char_budget:
            break
        body.append(chunk)
        used += len(chunk) + 1
    if not body:
        return ""
    return header + "\n".join(body)


# ---------------------------------------------------------------------------
# The trigger — "every 10 reports or monthly"
# ---------------------------------------------------------------------------
#
# There is no cron in the container, and three options were on the table.
#
# * **arq** — a pinned dependency, currently unused. Adopting it means a worker
#   process, a second thing that can be down, and a scheduler whose state lives
#   in Redis rather than in the database the findings live in. Too much new
#   surface for one periodic job.
# * **A startup check** — free, but it fires on deploy cadence, which is not a
#   cadence. A month with no deploy is a month with no audit; a day with six
#   deploys is six audits.
# * **An API endpoint driven by a dumb external heartbeat — chosen.**
#
# The point of the choice is where the *policy* lives. ``audit_is_due`` below
# holds "every 10 reports or monthly" in Python, next to the data it counts, and
# is testable without a scheduler. The scheduler therefore does not need to know
# the policy: it calls the endpoint on any convenient interval and the endpoint
# decides. A systemd timer matching the existing ``committee-bot.service``
# pattern is the intended driver:
#
#     # /etc/systemd/system/committee-consistency-audit.timer
#     [Timer]
#     OnCalendar=daily
#     Persistent=true
#
#     # /etc/systemd/system/committee-consistency-audit.service
#     [Service]
#     Type=oneshot
#     ExecStart=/usr/bin/curl -fsS -X POST http://127.0.0.1:8100/api/consistency/audit
#
# ``Persistent=true`` matters: a VPS reboot across the due date still runs the
# sweep on next boot instead of skipping the month. And because the due check is
# cheap and the sweep is idempotent, an over-eager timer is harmless — which is
# what makes the heartbeat allowed to be dumb.


async def audit_is_due() -> dict[str, Any]:
    """Whether a sweep is due: 10 new reports since the last one, or 30 days."""
    async with async_session() as session:
        last = (
            await session.execute(
                sql_text(
                    "SELECT started_at, corpus_size FROM consistency_audit_runs "
                    "WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1"
                )
            )
        ).mappings().first()
        corpus_size = int(
            (
                await session.execute(
                    sql_text(
                        "SELECT COUNT(DISTINCT evaluation_id) AS n FROM agent_outputs "
                        "WHERE agent_name = 'report_writer'"
                    )
                )
            ).scalar_one()
        )

    if last is None:
        return {"due": True, "reason": "no audit has ever run", "corpus_size": corpus_size}

    new_reports = corpus_size - int(last["corpus_size"] or 0)
    age_days = (datetime.now(timezone.utc) - last["started_at"]).days
    if new_reports >= AUDIT_EVERY_N_REPORTS:
        return {"due": True, "reason": f"{new_reports} new reports since last audit",
                "corpus_size": corpus_size}
    if age_days >= AUDIT_EVERY_N_DAYS:
        return {"due": True, "reason": f"last audit was {age_days} days ago",
                "corpus_size": corpus_size}
    return {
        "due": False,
        "reason": f"{new_reports}/{AUDIT_EVERY_N_REPORTS} new reports, "
                  f"{age_days}/{AUDIT_EVERY_N_DAYS} days since last audit",
        "corpus_size": corpus_size,
    }


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


@dataclass
class AuditResult:
    audit_run_id: str
    corpus_size: int
    claims_extracted: int
    conflicts_found: int
    findings_new: int
    findings_existing: int
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    verified: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


async def run_audit(*, verify: bool = True, persist: bool = True) -> AuditResult:
    """Observe -> check -> flag, over the whole corpus. Safe to run twice.

    Idempotency has two halves. Detection is a pure function of the corpus, so
    an unchanged corpus yields identical fingerprints; and ``record_finding``
    refuses to append a second revision-1 for a fingerprint it already holds. A
    second run therefore reports ``findings_new = 0`` and leaves the row count
    untouched.
    """
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc)

    rows = await load_corpus()
    aliases = _build_alias_map(await _project_names())
    claims = claims_from_corpus(rows, aliases)
    conflicts = detect_conflicts(claims)

    new = existing = 0
    rendered: list[dict[str, Any]] = []
    for conflict in conflicts:
        verification = await verify_candidate(conflict) if verify else None
        status: Status = "open"
        rationale = conflict.note
        if verification is not None and not verification.ok:
            status = "unverified"
            rationale = f"{conflict.note} {verification.detail}".strip()
        if persist:
            outcome = await record_finding(
                conflict, audit_run_id=run_id, verification=verification,
                status=status, rationale=rationale,
            )
            new += 1 if outcome["inserted"] else 0
            existing += 0 if outcome["inserted"] else 1
        payload = conflict.to_json()
        payload["status"] = status
        payload["fingerprint"] = conflict.fingerprint()
        payload["verification"] = verification.to_json() if verification else None
        rendered.append(payload)

    if persist:
        async with async_session() as session:
            await session.execute(
                sql_text(
                    "INSERT INTO consistency_audit_runs "
                    "(id, started_at, completed_at, status, corpus_size, claims_extracted, "
                    " conflicts_found, findings_new) "
                    "VALUES (:id, :started, :completed, 'completed', :corpus, :claims, "
                    "        :conflicts, :new)"
                ),
                {
                    "id": run_id, "started": started,
                    "completed": datetime.now(timezone.utc),
                    "corpus": len(rows), "claims": len(claims),
                    "conflicts": len(conflicts), "new": new,
                },
            )
            await session.commit()

    return AuditResult(
        audit_run_id=run_id, corpus_size=len(rows), claims_extracted=len(claims),
        conflicts_found=len(conflicts), findings_new=new, findings_existing=existing,
        conflicts=rendered, verified=verify,
    )
