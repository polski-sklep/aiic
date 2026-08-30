"""Did enough of the committee survive this run for its number to mean anything?

`evaluations.run_health` (orchestrator.build_run_health, migration 0005) already
records how much of the committee was alive. Nothing a human meets day-to-day
reads it. This module is the reading: it turns a run-health record into the
words that go next to the decision, and it is the ONE definition of what
"degraded" means, shared by the Telegram completion message and the
HTML/Markdown report so the two cannot say different things about the same run.

THE CASE IT EXISTS FOR
----------------------
Plasma `d5571fd9` (2026-04-12) lost six of its seven data agents — competitive
intel, field intel, governance, legal, on-chain, tech infra — to a
prompt-template bug. `_calc_score` sums the weights that scored and divides by
that sum, so the 0.45 of the weight table that survived was renormalised to 1.0.
The result was written to the ledger, rendered into a report and shown to a
human in exactly the format a whole-committee score takes. It sat there from
April until August.

The failure was not the degradation. Agents die; the pipeline is designed to
carry on. The failure was that the output of a half-dead committee was
**typographically identical** to the output of a live one.

WHAT THE MISSING WEIGHT CAN DO TO THE SCORE — and where the thresholds come from
-------------------------------------------------------------------------------
Let `c` be `score_weight_covered` and `m = 1 - c` the weight that never scored.
The reported score `S` is a weighted mean over the surviving weights, so the
whole-committee score the run was trying to compute is

    S_true = c*S + m*X        with X the (unknown) weighted mean of the missing
                              agents, X in [0, 100]

which puts `S_true` somewhere in `[c*S, c*S + 100*m]` — an interval exactly
`100*m` points wide. That is not a statistical estimate and carries no
distributional assumption; it is the full range the arithmetic permits, and it
is the honest thing to print beside a renormalised number.

The recommendation bands are PASS < 60 <= WATCH < 75 <= INVEST. The narrowest is
WATCH, fifteen points. So the severity cut is not a taste judgement:

  * `m >= 0.15` (coverage <= 0.85) — the missing weight alone spans a whole
    band. The number cannot be relied on to land in the right band at all,
    whatever its value. SEVERE.
  * `0 < m < 0.15` (coverage > 0.85) — the number can still move, and can still
    cross a boundary it happens to sit near, but it cannot skip a band.
    DEGRADED.
  * `m == 0` with agents nonetheless lost — the score is a whole-committee
    number and the loss is elsewhere in the committee (Ray, the devil's
    advocate and the technical analyst carry no score weight). MINOR: worth
    saying, not worth caveating the score over.

Two conditions are their own category regardless of coverage:

  * `risk_officer_ran = false`. `vetoed` is read off that agent's output, so an
    agent that never answered reads as an agent that cleared the project.
    Chainlink `75cf1b3d` is the live instance. PROJECT_DECISIONS D4 says a veto
    fires on presence of danger and never on absence of evidence; clearing on
    absence of evidence is the same error with the sign flipped. SEVERE.
  * `chair_decided = false`. There is no verdict, but there is still a score,
    and a score with no verdict beside it invites being read as one. SEVERE.

And `report_usable = false` outranks all of it: there is no deliverable, so
nothing in the message is a committee judgement. NO REPORT.

TWO CONSTRAINTS ON THIS FILE, BOTH LOAD-BEARING
-----------------------------------------------
1. **Standard library only, and no intra-package imports.** `telegram_bot.py`
   loads this file directly by path, exactly as it loads `app/llm/pricing.py`,
   because importing the package would execute `app/llm/__init__.py` ->
   `app/utils/types.py`, which uses `TypeAliasType` (3.12+). The bot runs under
   the VPS system interpreter, which is 3.10.12. A `from app.` import here would
   fail no test and would silently remove the warning from every message on the
   one machine that sends them — which is the original defect, rebuilt.

2. **Must parse and run on Python 3.10.** `from __future__ import annotations`
   keeps `X | Y` annotations legal; do not use them in a runtime position, and
   no `match`, no PEP 695 generics. `tests/test_degraded_warning.py` executes
   this file under a real 3.10 interpreter.

Nothing here may raise on bad input. A warning that can take down the message it
is attached to is worse than no warning: the message carries a report that cost
thirteen minutes and real money, and this is a caveat printed beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# COPIED CONSTANTS — pinned against their source by a test, never re-derived.
#
# These are `agents/orchestrator.py`'s, duplicated because this file may not
# import the app package (see the header). `tests/test_degraded_warning.py`
# asserts every one of them equals the orchestrator's, so a change there fails
# the suite here rather than drifting quietly. Do not edit a value in isolation.
# ---------------------------------------------------------------------------

#: `orchestrator.SCORE_WEIGHTS`.
SCORE_WEIGHTS = {
    "tokenomics_analyst": 0.15,
    "onchain_analyst": 0.12,
    "tech_infra_analyst": 0.15,
    "governance_analyst": 0.08,
    "competitive_intel": 0.10,
    "field_intel": 0.05,
    "risk_officer": 0.15,
    "maturation_scorer": 0.10,
    "legal_regulatory": 0.05,
    "portfolio_manager": 0.05,
}

#: `orchestrator.DATA_AGENT_NAMES` — the eight step-1 agents.
DATA_AGENT_NAMES = frozenset(
    {
        "tokenomics_analyst",
        "governance_analyst",
        "onchain_analyst",
        "tech_infra_analyst",
        "competitive_intel",
        "field_intel",
        "legal_regulatory",
        "technical_analyst",
    }
)

#: `orchestrator.INVEST_SCORE_THRESHOLD` / `WATCH_SCORE_THRESHOLD`.
INVEST_SCORE_THRESHOLD = 75.0
WATCH_SCORE_THRESHOLD = 60.0

#: Width of the narrowest recommendation band (WATCH, 60.0..75.0). The severity
#: cut below is this number and nothing else; see the header derivation.
NARROWEST_BAND = INVEST_SCORE_THRESHOLD - WATCH_SCORE_THRESHOLD

#: Coverage at or below which the weight that never scored can, on its own, span
#: a whole band. `1 - 0.85 == 0.15 == NARROWEST_BAND / 100`.
SEVERE_MAX_COVERAGE = round(1.0 - NARROWEST_BAND / 100.0, 3)


# ---------------------------------------------------------------------------
# Severity — ordered, because a run at 0.85 and a run at 0.45 are both degraded
# and saying so identically would repeat the flattening this exists to fix.
# ---------------------------------------------------------------------------

OK = "ok"
MINOR = "minor"
DEGRADED = "degraded"
SEVERE = "severe"
NO_REPORT = "no_report"

#: Ascending. `_worst` compares by index, so inserting a tier is a one-line edit.
SEVERITY_ORDER = (OK, MINOR, DEGRADED, SEVERE, NO_REPORT)

_HEADLINE = {
    MINOR: "PARTIAL RUN — MINOR",
    DEGRADED: "DEGRADED RUN",
    SEVERE: "DEGRADED RUN — SEVERE",
    NO_REPORT: "NO REPORT — this run produced no committee report",
}

_REPORT_FAILURE_DETAIL = {
    "call_failed": "the Report Writer's model call failed",
    "unparseable": "the Report Writer's response could not be parsed",
    "no_sections": "the Report Writer returned an object with no sections",
    "gate_failed": "the structural gate rejected the project before any agent ran",
}


def _worst(left: str, right: str) -> str:
    try:
        return left if SEVERITY_ORDER.index(left) >= SEVERITY_ORDER.index(right) else right
    except ValueError:
        return left


# ---------------------------------------------------------------------------
# Reading a run's health without the database
# ---------------------------------------------------------------------------


def agent_failed(record: Any) -> bool:
    """True when a serialised agent result produced nothing usable.

    Mirrors `orchestrator._agent_failed`, over `_ser`'s dict rather than the
    `AgentResult` — the shape the API returns in `agent_results` and the shape
    `agent_outputs` rows hold, which is the one shape both consumers already
    have in hand.
    """
    if not isinstance(record, dict):
        return True
    if record.get("error"):
        return True
    output = record.get("output")
    if not isinstance(output, dict):
        return False
    return "parse_error" in output or "error" in output


def _report_usable(agent_results: dict) -> tuple[bool, str | None]:
    """`(usable, reason)` for the Report Writer, per `report_deliverable_state`."""
    record = agent_results.get("report_writer")
    if not isinstance(record, dict):
        return False, "call_failed"
    output = record.get("output")
    if not isinstance(output, dict):
        output = {}
    sections = output.get("sections")
    if isinstance(sections, dict) and sections:
        return True, None
    if record.get("error") or "error" in output:
        return False, "call_failed"
    if "parse_error" in output:
        return False, "unparseable"
    return False, "no_sections"


def health_from_agent_results(agent_results: Any, status: Any = None) -> dict:
    """Reconstruct the run-health record from serialised agent results.

    The API's `POST /api/evaluate` response is filtered through
    `EvaluateResponse`, which does not carry `run_health` — the pipeline computes
    it, the database stores it, and the wire drops it. Rather than make the
    warning depend on a second HTTP round trip that can fail exactly when things
    are going wrong, it is recomputed here from `agent_results`, which the same
    response does carry in full.

    That is a second implementation of `build_run_health`, and the drift risk is
    real, so it is measured rather than asserted: the test suite runs this
    function over the persisted `agent_outputs` of all twenty production
    evaluations and asserts key-for-key agreement with the `run_health` the
    orchestrator itself wrote. If `run_health` ever does arrive on the wire,
    prefer it — `assess_evaluation` already does.

    `status` matters only for a terminal outcome the agent results cannot show:
    `gate_failed`, where no agent ran at all.
    """
    if not isinstance(agent_results, dict):
        return {}
    results = {k: v for k, v in agent_results.items() if isinstance(k, str)}

    if str(status or "") == "gate_failed":
        return {
            "report_usable": False,
            "report_failure_reason": "gate_failed",
            "agents_run": 0,
            "agents_failed": 0,
            "failed_agents": [],
            "data_agents_total": 0,
            "data_agents_failed": [],
            "score_weight_covered": None,
            "risk_officer_ran": False,
            "chair_decided": False,
        }

    failed = sorted(name for name, record in results.items() if agent_failed(record))
    data_total = [name for name in results if name in DATA_AGENT_NAMES]
    data_failed = sorted(name for name in failed if name in DATA_AGENT_NAMES)

    covered = 0.0
    for name, weight in SCORE_WEIGHTS.items():
        record = results.get(name)
        if isinstance(record, dict) and record.get("score") is not None:
            covered += weight
    total = sum(SCORE_WEIGHTS.values())

    usable, reason = _report_usable(results)
    chair = results.get("committee_chair")
    chair_output = chair.get("output") if isinstance(chair, dict) else None
    risk = results.get("risk_officer")

    return {
        "report_usable": usable,
        "report_failure_reason": reason,
        "agents_run": len(results),
        "agents_failed": len(failed),
        "failed_agents": failed,
        "data_agents_total": len(data_total),
        "data_agents_failed": data_failed,
        "score_weight_covered": round(covered / total, 3) if total else None,
        "risk_officer_ran": risk is not None and not agent_failed(risk),
        # Both halves, as in build_run_health: the Chair's result has to be
        # intact AND the decision has to be a decision.
        "chair_decided": bool(
            chair is not None
            and not agent_failed(chair)
            and isinstance(chair_output, dict)
            and chair_output.get("decision")
        ),
    }


# ---------------------------------------------------------------------------
# What the missing weight can do to the score
# ---------------------------------------------------------------------------


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def score_interval(score: Any, coverage: Any) -> tuple[float, float] | None:
    """Range the whole-committee score could have taken, given the missing weight.

    `None` when there is no score, no coverage, or nothing was missing — in the
    last case the reported score IS the whole-committee score, and an interval
    would only imply a doubt that is not there.
    """
    value = _float(score)
    covered = _float(coverage)
    if value is None or covered is None:
        return None
    if covered >= 1.0 or covered <= 0.0:
        return None
    low = covered * value
    return round(low, 1), round(low + 100.0 * (1.0 - covered), 1)


_BAND_EDGES = (
    ("PASS", 0.0, WATCH_SCORE_THRESHOLD),
    ("WATCH", WATCH_SCORE_THRESHOLD, INVEST_SCORE_THRESHOLD),
    ("INVEST", INVEST_SCORE_THRESHOLD, 100.0),
)


def bands_spanned(low: float, high: float) -> list[str]:
    """Every recommendation band the interval touches, lowest first."""
    return [name for name, lo, hi in _BAND_EDGES if low < hi and high >= lo]


# ---------------------------------------------------------------------------
# The assessment
# ---------------------------------------------------------------------------


@dataclass
class Assessment:
    """What to say about this run, and how loudly. Never raises; may be empty."""

    severity: str = OK
    #: One-line banner. "" when the run was whole.
    headline: str = ""
    #: Detail lines, in the order they should be printed. Empty when whole.
    lines: list[str] = field(default_factory=list)
    #: Suffix for the score line. "" when the score is a whole-committee number.
    score_caveat: str = ""
    coverage: float | None = None
    interval: tuple[float, float] | None = None
    #: Why this severity, as short machine-readable tags. For tests and logs.
    reasons: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.severity != OK

    def block(self) -> str:
        """The whole warning as one text block, or "" for a healthy run."""
        if not self.headline:
            return ""
        return "\n".join([self.headline] + self.lines)


def _agent_list(names: Any, limit: int = 8) -> str:
    if not isinstance(names, (list, tuple)):
        return ""
    shown = [str(n) for n in names]
    if len(shown) > limit:
        return ", ".join(shown[:limit]) + ", +%d more" % (len(shown) - limit)
    return ", ".join(shown)


def _pct(value: float) -> str:
    return "%g%%" % round(100.0 * value, 1)


def assess(health: Any, score: Any = None) -> Assessment:
    """Classify a run-health record. A whole run yields an empty Assessment.

    `health` is `evaluations.run_health`, or anything shaped like it —
    `health_from_agent_results` output included. Unknown keys are ignored and
    missing keys read as "not known to be broken", so a record written by an
    older or newer pipeline degrades to silence rather than to a false alarm.
    """
    if not isinstance(health, dict):
        return Assessment()

    coverage = _float(health.get("score_weight_covered"))
    interval = score_interval(score, coverage)
    severity = OK
    reasons: list[str] = []
    lines: list[str] = []

    failed_agents = health.get("failed_agents") or []
    failed_count = health.get("agents_failed")
    if not isinstance(failed_count, int) or isinstance(failed_count, bool):
        failed_count = len(failed_agents) if isinstance(failed_agents, list) else 0
    data_failed = health.get("data_agents_failed") or []
    data_total = health.get("data_agents_total")

    # --- 1. Is there a deliverable at all? Outranks everything below, and does
    #        not suppress it: the other findings are still facts about the run.
    if health.get("report_usable") is False:
        severity = _worst(severity, NO_REPORT)
        reasons.append("no_report")
        detail = _REPORT_FAILURE_DETAIL.get(
            str(health.get("report_failure_reason")),
            "the Report Writer produced no report",
        )
        lines.append(
            "There is no committee report for this run: %s. Nothing below is a "
            "committee judgement." % detail
        )

    # --- 2. The seat that holds the veto.
    if health.get("risk_officer_ran") is False:
        severity = _worst(severity, SEVERE)
        reasons.append("risk_officer_absent")
        lines.append(
            "The Risk Officer did not answer. `vetoed` is read off its output, so "
            '"no veto" on this run means the question was never asked — not that '
            "the project cleared."
        )

    # --- 3. A score with no verdict beside it.
    if health.get("chair_decided") is False:
        severity = _worst(severity, SEVERE)
        reasons.append("chair_absent")
        lines.append(
            "The Chair returned no decision. Any score here is an arithmetic "
            "result that nobody adjudicated."
        )

    # --- 4. How much of the weight table actually scored.
    if coverage is not None and coverage <= 0.0:
        reasons.append("no_score")
        lines.append("No agent returned a score, so there is no committee score at all.")
    elif coverage is not None and coverage < 1.0:
        if coverage <= SEVERE_MAX_COVERAGE:
            severity = _worst(severity, SEVERE)
            reasons.append("coverage_severe")
        else:
            severity = _worst(severity, DEGRADED)
            reasons.append("coverage_degraded")

        sentence = (
            "The score was renormalised over the %s of the weight table that "
            "scored, so it is printed as a whole-committee number and is not one."
            % _pct(coverage)
        )
        if interval is not None:
            low, high = interval
            spanned = bands_spanned(low, high)
            sentence += " With the missing %s at its best and worst the true score is anywhere in %g–%g" % (
                _pct(1.0 - coverage),
                low,
                high,
            )
            sentence += (
                " (%s)." % "/".join(spanned) if len(spanned) > 1 else " (still %s)." % spanned[0]
            )
        lines.append(sentence)

    # --- 5. Agents lost that cost no score weight. Real, and not the same thing.
    if severity == OK and failed_count:
        severity = MINOR
        reasons.append("non_scoring_failures")
        lines.append(
            "No score weight was lost — the score is a whole-committee number — "
            "but the report is missing their contribution."
        )

    if severity == OK:
        return Assessment(coverage=coverage)

    # The count leads: it is the fact that sizes everything else. The data-agent
    # split is parenthesised rather than appended, so the roster that follows
    # cannot be misread as naming the data agents alone.
    if failed_count:
        count = "%d of %s agents failed" % (failed_count, health.get("agents_run") or "?")
        if data_failed and data_total:
            count += " (%d of %d data agents)" % (len(data_failed), data_total)
        listing = _agent_list(failed_agents)
        lines.insert(0, count + (": %s." % listing if listing else "."))

    # A reconstructed judgement is not a live one, and the difference is not
    # cosmetic: a backfilled record was inferred from what survived in
    # `agent_outputs`, so an agent that died before it could be persisted at all
    # is invisible to it and the true damage can only be worse than stated.
    if health.get("backfilled"):
        lines.append(
            "This health record was reconstructed from the persisted agent outputs "
            "after the fact, not observed while the run happened."
        )

    caveat = ""
    if coverage is not None and 0.0 < coverage < 1.0 and _float(score) is not None:
        caveat = " [%s of weights — see above]" % _pct(coverage)

    return Assessment(
        severity=severity,
        headline=_HEADLINE[severity],
        lines=lines,
        score_caveat=caveat,
        coverage=coverage,
        interval=interval,
        reasons=reasons,
    )


def assess_evaluation(data: Any) -> Assessment:
    """`assess` over a raw `POST /api/evaluate` response. Never raises.

    Prefers a `run_health` that arrived on the wire; falls back to recomputing it
    from `agent_results`. Any exception at all yields an empty Assessment — a
    broken warning must not be able to cost the message it is attached to.
    """
    try:
        if not isinstance(data, dict):
            return Assessment()
        health = data.get("run_health")
        if not isinstance(health, dict):
            health = health_from_agent_results(data.get("agent_results"), data.get("status"))
        return assess(health, data.get("overall_score"))
    except Exception:
        return Assessment()
