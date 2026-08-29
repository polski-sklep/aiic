"""9-Step Committee Orchestrator Pipeline.

Step 0: Protocol Resolution - resolve project identity, fetch baseline data
Step 1: Intelligence Gathering - 8 data agents run in parallel
 GATE:  Structural Check - hard limits, mandate exclusions
Step 4: Maturation Scoring - growth stage assessment
Step 5: Risk + Stress - Risk Officer with VETO POWER
Step 5b: Devil's Advocate - contrarian challenge
Step 6: Portfolio Assessment - fit, sizing, correlation
Step 7: Report + Thesis Assembly - 24-section structured report
Post:  Ray Dalio - independent contrarian Sonnet pass
Step 8: Committee Decision - Chair makes final BUY/PASS/WATCH/VETO
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from app.agents.base import AgentResult, BaseAgent
from app.agents.chair import CommitteeChair
from app.agents.data_agents import (
    CompetitiveIntel,
    FieldIntel,
    GovernanceAnalyst,
    LegalRegulatory,
    OnChainAnalyst,
    TechInfraAnalyst,
)
from app.agents.guardrails import run_structural_gate
from app.agents.ray import RayDalio
from app.agents.reconciliation import (
    build_case_context,
    fetch_canonical_defi_facts,
    reconcile_data,
    render_contradictions,
)
from app.agents.report_writer import ReportWriter
from app.agents.risk_officer import RiskOfficer
from app.agents.synthesis_agents import DevilsAdvocate, MaturationScorer, PortfolioManager
from app.agents.technical_analyst import TechnicalAnalyst
from app.agents.tokenomics import TokenomicsAnalyst
from app.utils.citations import build_source_catalog
from app.utils.types import JSONObject, ScoreReconciliation

logger = logging.getLogger(__name__)


def _render_prior_context(prior) -> str:
    """Bounded prompt text for a prior evaluation; "" when there is none.

    Imported lazily so that `app.agents.orchestrator` keeps importing cleanly
    without a database, which the test suite relies on.
    """
    if prior is None:
        return ""
    try:
        from app.knowledge.history import render_prior_context

        return render_prior_context(prior)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Prior-evaluation rendering failed: %s", exc)
        return ""


class _SkipCalibration(Exception):
    """Raised to skip the calibration write for a run with no real verdict."""


# --- What `evaluations.status` means ---------------------------------------
#
# ONE question, and only one: did this run produce the artefact it exists to
# produce — a 24-section committee report?
#
# Every existing reader of the column tests `== "completed"` and nothing
# enumerates the failure values (api/reports.py `_load_report_parts` and
# `_list_report_rows`, knowledge/history.py `_build_prior`). That is what makes
# a new terminal value safe to add: a reader that does not know the word
# `report_failed` still classifies it as "not a success", which is correct.
#
# Degradation is deliberately NOT in here. A run where three data agents died
# but the report was written did produce its artefact, and every `== completed`
# reader is right to include it. How degraded it was is a separate axis, and it
# lives in `evaluations.run_health` (build_run_health, below).
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
#: The pipeline ran to the end but the Report Writer produced no `sections`.
#: Distinct from `failed` because the two leave very different wreckage: a
#: `failed` run raised out of api/evaluate.py and persisted nothing (the one
#: such row in production, ENS c8f3947d, has zero agent_outputs), while this
#: one has fourteen agents' worth of paid output on disk and is re-adjudicable.
#: Collapsing them would destroy exactly the distinction this exists to record.
STATUS_REPORT_FAILED = "report_failed"
#: The structural gate rejected the project before any agent ran. The
#: orchestrator has always returned this in its result dict; api/evaluate.py
#: used to throw it away and write `completed`.
STATUS_GATE_FAILED = "gate_failed"
#: The pipeline raised. Written by api/evaluate.py's exception handler only.
STATUS_FAILED = "failed"

#: Terminal statuses that mean "no report was produced". Exported so a caller
#: can ask the question without hardcoding the vocabulary.
NO_REPORT_STATUSES = frozenset({STATUS_REPORT_FAILED, STATUS_GATE_FAILED, STATUS_FAILED})

StatusCallback = Callable[[str, str, JSONObject], Awaitable[None]] | None

# Recommendation bands for the weighted committee score (AIIC_HANDOFF.md §3):
#   >= 75  INVEST   ·   60-74  WATCH   ·   < 60  PASS
#
# Defined once, here, because two call sites need them: `_simple_rec`, which
# runs only on the report-failure fallback branch, and `score_band`, which
# classifies the weighted score so a contradiction with the Chair's decision
# can be detected. Two copies of a threshold is how they drift.
INVEST_SCORE_THRESHOLD = 75.0
WATCH_SCORE_THRESHOLD = 60.0

# The decision string each band would imply if the score alone decided. It does
# not — the Chair decides — but the mapping is what makes "the score and the
# decision disagree" a statement with a truth value.
_BAND_DECISION: dict[str, str] = {"INVEST": "BUY", "WATCH": "WATCH", "PASS": "PASS"}

# Decisions that can be compared against a band. VETO is excluded because a veto
# is imposed by the Risk Officer and overrides the Chair unconditionally, so a
# VETO/INVEST pair is not the Chair contradicting the score. INSUFFICIENT_DATA
# is excluded because it is the absence of a decision.
_COMPARABLE_DECISIONS = frozenset(_BAND_DECISION.values())
_DECISION_BAND: dict[str, str] = {v: k for k, v in _BAND_DECISION.items()}


# Band ordering, so "how far apart" is a number and not a vibe.
_BAND_RANK: dict[str, int] = {"PASS": 0, "WATCH": 1, "INVEST": 2}


# The weighted-score table. Lifted out of `_calc_score` unchanged — same ten
# names, same ten values — because `build_run_health` has to report what
# fraction of this weight actually carried a score, and a second copy of the
# table is how the two answers drift apart.
#
# D6/PROJECT_DECISIONS: nothing here changes scoring. `_calc_score` reads this
# dict and computes exactly what it computed before.
SCORE_WEIGHTS: dict[str, float] = {
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


def score_band(score: float | None) -> str | None:
    """Which recommendation band a score falls in, or None."""
    if score is None:
        return None
    if score >= INVEST_SCORE_THRESHOLD:
        return "INVEST"
    if score >= WATCH_SCORE_THRESHOLD:
        return "WATCH"
    return "PASS"


def _coerce_score(value: object) -> float | None:
    """A score out of an LLM payload, or None. Never raises.

    The Report Writer is asked for `<weighted average>` and returns whatever it
    returns — a float, a string, "N/A", or nothing at all.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def extract_chair_visible_score(draft_report: JSONObject) -> tuple[float | None, str]:
    """The score the Chair actually reads, and where it came from.

    This is NOT `_calc_score`'s weighted number. It is the Report Writer's own
    `sections.22_overall_score` (or its top-level `score`), which is an LLM
    asked to produce a weighted average and given no weights. Recovering it is
    what distinguishes a divergence between two estimators from the Chair
    contradicting the evidence it was shown.
    """
    if not isinstance(draft_report, dict):
        return None, "none"

    sections = draft_report.get("sections")
    if isinstance(sections, dict):
        score = _coerce_score(sections.get("22_overall_score"))
        if score is not None:
            return score, "sections.22_overall_score"

    score = _coerce_score(draft_report.get("score"))
    if score is not None:
        return score, "draft_report.score"

    # The orchestrator's own fallback shape, built above when the Report Writer
    # emitted no `sections` key. Its `overall_score` IS `_calc_score`'s value,
    # so it cannot diverge from the weighted score by construction.
    score = _coerce_score(draft_report.get("overall_score"))
    if score is not None:
        return score, "draft_report.overall_score (orchestrator fallback: equals the weighted score)"

    return None, "none"


def _bands_apart(left: str | None, right: str | None) -> int | None:
    if left is None or right is None:
        return None
    return abs(_BAND_RANK[left] - _BAND_RANK[right])


def build_score_reconciliation(
    overall: float | None,
    draft_report: JSONObject,
    decision: str,
    chair_confidence: str,
    vetoed: bool,
) -> ScoreReconciliation:
    """Record the committee's two scores against the Chair's decision.

    Pure and side-effect free. It measures the incoherence; it does not resolve
    it. Nothing here feeds back into `decision`, into `_calc_score`, or into any
    prompt — per PROJECT_DECISIONS.md D6 the choice of what to *do* about it is
    Jacob's, and this exists so that choice can be made on a measured rate
    rather than on the single Aave row.

    Keeps `divergence` (the two scores disagree with each other) apart from
    `contradiction` (the score the Chair saw disagrees with the Chair's
    decision), because they are different defects with different fixes and the
    system currently cannot tell them apart. Aave was divergence.
    """
    weighted_band = score_band(overall)
    visible, source = extract_chair_visible_score(draft_report)
    visible_band = score_band(visible)
    thresholds = {"invest": INVEST_SCORE_THRESHOLD, "watch": WATCH_SCORE_THRESHOLD}

    divergence = None if (overall is None or visible is None) else weighted_band != visible_band
    score_delta = None if (overall is None or visible is None) else round(overall - visible, 2)
    divergence_bands = _bands_apart(weighted_band, visible_band)

    comparable = overall is not None and not vetoed and decision in _COMPARABLE_DECISIONS

    contradiction: bool | None = None
    contradiction_bands: int | None = None
    apparent: bool | None = None
    apparent_bands: int | None = None

    if comparable:
        apparent = _BAND_DECISION[weighted_band] != decision
        apparent_bands = _bands_apart(weighted_band, _DECISION_BAND[decision])
        if visible_band is not None:
            contradiction = _BAND_DECISION[visible_band] != decision
            contradiction_bands = _bands_apart(visible_band, _DECISION_BAND[decision])

    conflict = bool(divergence) or bool(contradiction) or bool(apparent)

    if overall is None:
        detail = "No weighted score was computable, so there is nothing to compare."
    elif vetoed:
        detail = (
            f"Risk Officer veto overrides the Chair, so the {weighted_band} band "
            f"({overall}) is not comparable against the recorded VETO."
        )
    elif decision not in _COMPARABLE_DECISIONS:
        detail = (
            f"Decision {decision!r} is not one of BUY/WATCH/PASS, so the "
            f"{weighted_band} band ({overall}) has nothing to disagree with."
        )
    else:
        parts = []
        if visible is None:
            parts.append(
                f"The Chair's report carried no readable score, so only the "
                f"ledger view is available: weighted {overall} is {weighted_band}, "
                f"the Chair returned {decision}."
            )
        else:
            parts.append(
                f"The Chair read {visible} ({visible_band}, from {source}); the "
                f"ledger stores the weighted {overall} ({weighted_band})."
            )
            parts.append(
                "The two scores diverge by "
                f"{score_delta:+} across {divergence_bands} band(s)."
                if divergence
                else f"The two scores agree on band ({weighted_band}), delta {score_delta:+}."
            )
            parts.append(
                f"Against the number it actually saw, the Chair's {decision} "
                f"is {'a contradiction' if contradiction else 'consistent'}"
                f"{f' ({contradiction_bands} band(s) apart)' if contradiction else ''}."
            )
        parts.append(
            f"Read from the ledger alone the decision looks "
            f"{'contradictory' if apparent else 'consistent'}"
            f"{f' ({apparent_bands} band(s) apart)' if apparent else ''}."
        )
        detail = " ".join(parts)

    return {
        "weighted_score": overall,
        "weighted_band": weighted_band,
        "chair_visible_score": visible,
        "chair_visible_band": visible_band,
        "chair_visible_source": source,
        "chair_decision": decision,
        "chair_confidence": chair_confidence,
        "comparable": comparable,
        "divergence": divergence,
        "score_delta": score_delta,
        "divergence_bands_apart": divergence_bands,
        "contradiction": contradiction,
        "contradiction_bands_apart": contradiction_bands,
        "apparent_contradiction": apparent,
        "apparent_bands_apart": apparent_bands,
        "conflict": conflict,
        "detail": detail,
        "thresholds": thresholds,
    }


# Words that carry no discriminating signal when matching one agent's phrasing
# of a risk against another's.
_RISK_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "cannot", "could", "for",
    "from", "has", "have", "in", "is", "it", "its", "may", "might", "not", "of",
    "on", "or", "risk", "risks", "that", "the", "there", "this", "to", "very",
    "which", "will", "with", "would",
})

# Two risk statements are treated as the same risk when their token sets overlap
# by at least this much. Measured on a six-agent convergent-risk fixture:
# genuinely duplicate pairs scored 0.455-0.875, unrelated pairs 0.000-0.125.
# 0.40 sits in the middle of that empty band with margin on both sides.
#
# Erring toward NOT merging is the right bias: a false merge destroys a distinct
# risk from the only surviving record of the committee's thinking, whereas a
# false split merely fails to surface a convergence.
_RISK_MATCH_THRESHOLD = 0.40


def _risk_tokens(text: str) -> set[str]:
    """Normalised token set for one risk statement.

    Word order is deliberately discarded. The old key was
    `f"[{agent}] {risk}"[:50].lower()` — a prefix of a string that *began with
    the agent name*, so 15-25 of its 50 characters were exactly the part that
    differs between agents. It deduplicated nothing across agents, which is the
    only place duplication actually occurs. A prefix key also cannot survive
    paraphrase, and these statements are LLM prose: six agents naming one
    September 2026 unlock wrote it six different ways.
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    tokens = set()
    for word in cleaned.split():
        if word in _RISK_STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.add(word)
    return tokens


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def dedupe_risks(results: dict[str, AgentResult], skip: set[str], limit: int = 5) -> list[JSONObject]:
    """Merge the agents' risk lists and rank by how many agents named each one.

    For five of the six projects in the calibration corpus, the Notion block
    this feeds is the only surviving record of what the committee thought the
    risks were (CONTRACTS §2.5) — so whatever it drops is gone.

    Two defects were stacked in the old version. The key included the agent name
    (see `_risk_tokens`), so cross-agent duplicates all survived; and the final
    `[:5]` then took the first five in `results` insertion order, i.e. data-agent
    registration order, so "Key Risks" was mostly `tokenomics_analyst`'s list
    rather than the committee's.

    Ranking by distinct-agent count surfaces convergence instead, which is the
    signal worth keeping: Plasma is the corpus's one clean HIT precisely because
    six agents independently named the same dated unlock, and that convergence
    is invisible in the artefact the old code stored. Ties keep first-seen
    order, so the output is deterministic.

    Matching is single-linkage over Jaccard similarity — approximate, as any
    paraphrase matching must be, and biased toward leaving risks separate.
    """
    clusters: list[JSONObject] = []

    for agent_name, result in results.items():
        if agent_name in skip or result.error or not isinstance(result.output, dict):
            continue
        risks = result.output.get("risks")
        if not isinstance(risks, list):
            continue

        for risk in risks:
            text = str(risk).strip()
            if not text:
                continue
            tokens = _risk_tokens(text)
            if not tokens:
                continue

            match = None
            for cluster in clusters:
                if max(_jaccard(tokens, member) for member in cluster["token_sets"]) >= _RISK_MATCH_THRESHOLD:
                    match = cluster
                    break

            if match is None:
                clusters.append({
                    "text": text,
                    "agents": [agent_name],
                    "token_sets": [tokens],
                    "order": len(clusters),
                })
                continue

            match["token_sets"].append(tokens)
            if agent_name not in match["agents"]:
                match["agents"].append(agent_name)
            # Keep the fullest phrasing of a convergent risk.
            if len(text) > len(match["text"]):
                match["text"] = text

    ranked = sorted(clusters, key=lambda c: (-len(c["agents"]), c["order"]))
    return [
        {"text": c["text"], "agents": c["agents"], "agent_count": len(c["agents"])}
        for c in ranked[:limit]
    ]


def format_risk_block(risks: list[JSONObject]) -> str:
    """Render ranked risks for the Notion page, keeping attribution visible."""
    if not risks:
        return ""
    lines = ["", "---", "", "**Key Risks:**"]
    for index, risk in enumerate(risks, start=1):
        agents = ", ".join(risk["agents"])
        if risk["agent_count"] > 1:
            lines.append(
                f"\n{index}. {risk['text']}  \n"
                f"   _named independently by {risk['agent_count']} agents: {agents}_"
            )
        else:
            lines.append(f"\n{index}. [{agents}] {risk['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run health — did this run produce its artefact, and how much of the committee
# was actually alive when it did?
# ---------------------------------------------------------------------------


def report_deliverable_state(result: AgentResult) -> tuple[bool, str | None]:
    """``(usable, reason)`` for the Report Writer's output.

    The deliverable is the 24-section structured report, and `sections` is the
    only key that carries it: api/reports.py addresses sections by name,
    knowledge/consistency.py extracts claims per section, and
    chair.format_report_for_chair renders sections. A payload without them
    satisfies none of those consumers, whatever else it contains.

    Two failure modes reach this function and both are recorded, because they
    need different remedies:

    * ``call_failed`` — the model call itself did not return. Every one of the
      four in production is ``Error code: 429 ... exceeded your current quota``,
      and in all four the *whole committee* died with it (14 of 14, 15 of 15
      agents errored). Transient; a re-run is the remedy.
    * ``unparseable`` — the model answered and the answer would not parse. On
      Hyperliquid e2d96b62 the response ran to 8,436 output tokens and stopped
      mid-string inside the object, ~21.5 KB of JSON that never closed.

      This was first written up as "not transient: the same prompt will hit the
      same ceiling". The database contradicts that and the correction matters,
      because it is the difference between "re-running is futile" and
      "re-running is the remedy". Measured on the corpus: the same project was
      re-run two hours later as be8210d4 and its Report Writer returned 55,345
      bytes that parsed cleanly, against e2d96b62's 22,382 that did not. Same
      prompt shape, same day, 2.5x the output, no truncation. The ceiling is
      stochastic, not deterministic, so a re-run is a real remedy here too —
      it is simply not one the pipeline can take for free, because unlike a
      429 there is no reason to expect the second attempt to be cheap.

      What is NOT transient about e2d96b62 is how much went with it: the
      Report Writer, Ray and the Chair all hit the same wall on the same run
      (22,382 / 11,290 / 11,480 bytes, all three falling back to raw_output),
      while all twelve other agents returned full structured output and
      scored. The whole synthesis tier failed at once and the data tier was
      untouched.

    On ``unparseable`` the output holds `summary` and `raw_output`, which looks
    like a degraded success and is not one. agents/base.py already settled the
    identical question one stage later: a Chair that hits its output ceiling
    mid-JSON is recorded as CHAIR_FAILED and kept out of the ledger, because
    "a parse failure is not a verdict". A parse failure is not a report either,
    and the argument is stronger here — see the note at the call site on what
    the Chair is left holding.
    """
    output = result.output if isinstance(result.output, dict) else {}
    sections = output.get("sections")
    if isinstance(sections, dict) and sections:
        return True, None
    if result.error or "error" in output:
        return False, "call_failed"
    if "parse_error" in output:
        return False, "unparseable"
    # No sections, no error, no parse_error: the model returned a well-formed
    # object that simply is not a report. Rare, still not a deliverable.
    return False, "no_sections"


#: The eight step-1 agents, taken from the classes rather than written out, so
#: adding a ninth cannot leave this list behind.
DATA_AGENT_NAMES = frozenset(
    {
        TokenomicsAnalyst.name,
        GovernanceAnalyst.name,
        OnChainAnalyst.name,
        TechInfraAnalyst.name,
        CompetitiveIntel.name,
        FieldIntel.name,
        LegalRegulatory.name,
        TechnicalAnalyst.name,
    }
)

#: Agents whose failure degrades a run without destroying it.
#:
#: The eight data agents run in parallel and the synthesis layer is designed to
#: tolerate gaps — docs/CONTRACTS.md §4.2 makes their mutual independence the
#: design — so one of them dying costs coverage, not the artefact. The four
#: synthesis agents added here each consume the others' work and none of them
#: is the deliverable: Ray is a second opinion on a report that still exists,
#: and maturation, the devil's advocate and the portfolio manager each
#: contribute a slice of the score.
#:
#: Two agents are deliberately absent. The Report Writer is the artefact and has
#: no redundancy — nothing else in the pipeline assembles the sections, so its
#: failure is terminal rather than degrading. The Chair is the verdict, and the
#: pipeline already treats its failure as its own outcome (CHAIR_FAILED).
#: `risk_officer` is also absent: it is neither, and see build_run_health.
_DEGRADATION_ONLY = DATA_AGENT_NAMES | {
    MaturationScorer.name,
    DevilsAdvocate.name,
    PortfolioManager.name,
    RayDalio.name,
}


def build_run_health(
    agent_results: dict[str, AgentResult],
    report_usable: bool,
    report_failure_reason: str | None,
    decision: str | None,
    vetoed: bool,
) -> JSONObject:
    """A queryable record of how much of the committee survived this run.

    Instrument only — nothing here feeds a prompt, a score or a decision.

    WHY THIS IS SEPARATE FROM `status`. Three of the sixteen persisted runs
    finished with a real report and a damaged committee, and the record cannot
    currently say so:

    * Plasma d5571fd9 — six of the eight data agents died on a prompt-template
      bug (``Invalid format specifier``). `_calc_score` sums the weights that
      *did* score and divides by that sum, so the 0.45 of the weight table that
      survived was renormalised to 1.0 and the resulting number is
      indistinguishable, in the ledger and in the report, from one computed on
      the whole committee. `score_weight_covered` is that fraction, recorded.
    * Chainlink 75cf1b3d — the Risk Officer exhausted its tool rounds and
      returned nothing. `vetoed` is read as ``risk.output.get("veto", False)``,
      so an agent that never answered reads as an agent that cleared the
      project. Settled decision 1 (AIIC_HANDOFF §11, PROJECT_DECISIONS D4) is
      that a veto fires on presence of danger and never on absence of evidence;
      the converse — clearing on absence of evidence — is the same error with
      the sign flipped, and it is live. `risk_officer_ran` records it. Whether
      it should also be terminal is a governance question about what the
      committee is allowed to decide, so it is reported, not decided here.
    * GMX 8e4b3c83 — the Chair errored; `chair_decided` records it.

    None of those three should stop being `completed`: the report exists, the
    consistency sweep should read it and the retrospective should grade it.
    They should simply stop looking identical to a clean run.
    """
    failed = sorted(
        name for name, result in agent_results.items() if _agent_failed(result)
    )
    data_agents = [name for name in agent_results if name in DATA_AGENT_NAMES]
    data_failed = [name for name in failed if name in DATA_AGENT_NAMES]

    covered = sum(
        weight
        for name, weight in SCORE_WEIGHTS.items()
        if (result := agent_results.get(name)) is not None and result.score is not None
    )
    total = sum(SCORE_WEIGHTS.values())

    risk = agent_results.get("risk_officer")
    chair = agent_results.get("committee_chair")

    return {
        "report_usable": report_usable,
        "report_failure_reason": report_failure_reason,
        "agents_run": len(agent_results),
        "agents_failed": len(failed),
        "failed_agents": failed,
        # Failures that cost coverage rather than the artefact. Split out so
        # "a degraded run" and "a broken run" are answerable separately.
        "degraded_only_failures": sorted(n for n in failed if n in _DEGRADATION_ONLY),
        "data_agents_total": len(data_agents),
        "data_agents_failed": sorted(data_failed),
        # Fraction of `SCORE_WEIGHTS` that actually carried a score. 1.0 is a
        # whole committee; anything less means `overall_score` was renormalised
        # over a subset and is a weaker number than it looks.
        "score_weight_covered": round(covered / total, 3) if total else None,
        "risk_officer_ran": risk is not None and not _agent_failed(risk),
        # Both halves matter: the Chair's result has to be intact AND the
        # decision has to be a decision. `_simple_rec` can return
        # INSUFFICIENT_DATA from a healthy Chair, and CHAIR_FAILED is written
        # by evaluate() when the Chair returned no `decision` key at all.
        "chair_decided": (
            chair is not None
            and not _agent_failed(chair)
            and bool(decision)
            and decision not in {"CHAIR_FAILED", "INSUFFICIENT_DATA"}
        ),
        "vetoed": bool(vetoed),
    }


def _agent_failed(result: AgentResult) -> bool:
    """True when an agent produced nothing usable.

    Matches how the rest of the pipeline already reads a broken agent: an
    `AgentResult.error`, or base.py's `parse_output` fallback, whose signature
    is a `parse_error` key. `chair_failed` in `evaluate()` uses the same pair.
    """
    if result.error:
        return True
    output = result.output if isinstance(result.output, dict) else {}
    return "parse_error" in output or "error" in output


def aggregate_data_quality(results: dict[str, AgentResult]) -> JSONObject:
    """Roll the agents' own `data_quality` blocks up into one record.

    Every agent is asked for `data_quality` — `verified_claims`,
    `inferred_claims`, `unknown_gaps` (agents/base.py:108) — and separately for
    a `confidence` of low/medium/high. Nothing has ever connected the two and
    nothing aggregated the first upward, so an assessment resting entirely on
    inference could be reported at high confidence and no part of the system
    would notice. The corpus that exists today was produced under heavy
    CoinGecko 429 degradation, which is precisely the condition this makes
    visible.

    Instrument only. It does NOT derive, adjust or override any agent's
    `confidence`, and it changes no prompt: how the committee reports certainty
    is a semantics decision that belongs with the conviction question already
    going to Jacob, the same boundary Task 1 holds.
    """
    per_agent: dict[str, JSONObject] = {}
    verified_total = 0
    inferred_total = 0
    gaps: list[str] = []
    confidence_counts: dict[str, int] = {}

    for name, result in results.items():
        if result.error or not isinstance(result.output, dict):
            continue
        block = result.output.get("data_quality")
        confidence = result.output.get("confidence")
        if isinstance(confidence, str) and confidence:
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        if not isinstance(block, dict):
            continue

        verified = _coerce_count(block.get("verified_claims"))
        inferred = _coerce_count(block.get("inferred_claims"))
        raw_gaps = block.get("unknown_gaps")
        agent_gaps = [str(gap) for gap in raw_gaps if gap] if isinstance(raw_gaps, list) else []

        verified_total += verified
        inferred_total += inferred
        gaps.extend(f"[{name}] {gap}" for gap in agent_gaps)

        claims = verified + inferred
        per_agent[name] = {
            "verified_claims": verified,
            "inferred_claims": inferred,
            "unknown_gaps": len(agent_gaps),
            "verified_ratio": round(verified / claims, 3) if claims else None,
            "confidence": confidence if isinstance(confidence, str) else None,
        }

    claims_total = verified_total + inferred_total
    return {
        "agents_reporting": len(per_agent),
        "agents_missing_data_quality": len(
            [n for n, r in results.items() if not r.error and n not in per_agent]
        ),
        "verified_claims": verified_total,
        "inferred_claims": inferred_total,
        "verified_ratio": round(verified_total / claims_total, 3) if claims_total else None,
        "unknown_gap_count": len(gaps),
        "unknown_gaps": gaps[:40],
        "confidence_distribution": confidence_counts,
        "per_agent": per_agent,
    }


def _coerce_count(value: object) -> int:
    """A non-negative integer out of an LLM payload. Never raises."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(float(value.strip())), 0)
        except ValueError:
            return 0
    return 0


def _reconcile(
    outputs: dict[str, JSONObject], case_context: JSONObject, scope: str
) -> JSONObject:
    """Run one reconciliation pass. Cannot fail an evaluation.

    The original call was unwrapped, which was defensible when `reconcile_data`
    only walked numeric JSON leaves. It now runs a regex extractor over every
    string in every agent's output, and the work is a guard: nothing downstream
    needs it to have succeeded. An evaluation that dies because its consistency
    check tripped has lost fifteen agents' worth of paid model calls to a
    warning system.

    `reconcile_data` already contains the prose pass in its own try/except and
    degrades to the structured result. This is the outer belt for the rest —
    a malformed `case_context`, an import failure, anything.
    """
    try:
        result = reconcile_data(outputs, case_context, scope)
    except Exception as exc:
        logger.warning("Reconciliation pass %r failed (non-fatal): %s", scope, exc)
        return {
            "scope": scope,
            "status": "UNAVAILABLE: %s" % exc,
            "inconsistencies_found": 0,
            "inconsistencies": [],
            "contradictions_found": 0,
            "contradictions": [],
        }
    if result.get("inconsistencies_found", 0) > 0:
        logger.warning(
            "Reconciliation (%s): %d structured inconsistencies",
            scope,
            result["inconsistencies_found"],
        )
    return result


class Orchestrator:
    """9-step committee evaluation pipeline with 15 agents."""

    def __init__(self):
        self.data_agents: list[BaseAgent] = [
            TokenomicsAnalyst(),
            GovernanceAnalyst(),
            OnChainAnalyst(),
            TechInfraAnalyst(),
            CompetitiveIntel(),
            FieldIntel(),
            LegalRegulatory(),
            TechnicalAnalyst(),
        ]
        self.maturation = MaturationScorer()
        self.devils_advocate = DevilsAdvocate()
        self.risk_officer = RiskOfficer()
        self.portfolio_manager = PortfolioManager()
        self.report_writer = ReportWriter()
        self.chair = CommitteeChair()
        self.ray = RayDalio()

    async def evaluate(
        self,
        project_name: str,
        project_info: JSONObject | None = None,
        knowledge_context: str = "",
        current_portfolio: list[JSONObject] | None = None,
        on_status: StatusCallback = None,
        evaluation_id: str | None = None,
    ) -> JSONObject:
        """Run the 15-agent pipeline.

        ``evaluation_id`` is the id of the ``evaluations`` row this run belongs
        to. It is threaded into ``record_calibration`` so that every calibration
        record can be joined back to the reasoning that produced it. Callers
        that bypass the API (e.g. one-off harness scripts) may leave it None,
        but the resulting record will be orphaned — all eight production rows
        written before this parameter existed have ``evaluation_id IS NULL``.
        """
        project_info = project_info or {}
        agent_results: dict[str, AgentResult] = {}
        context: JSONObject = {
            "project_name": project_name,
            "project_info": project_info,
            "knowledge_context": knowledge_context,
            "current_portfolio": current_portfolio or [],
        }

        def refresh_context() -> None:
            context["prior_agent_outputs"] = {
                name: result.output
                for name, result in agent_results.items()
                if not result.error
            }
            context["prior_agent_results"] = {
                name: {
                    "output": result.output,
                    "score": result.score,
                    "sources": result.sources,
                    "model_used": result.model_used,
                }
                for name, result in agent_results.items()
                if not result.error
            }
            context["source_catalog"] = build_source_catalog(agent_results)

        if on_status:
            await on_status("step", "0_protocol_resolution", {})
        resolved = await self._resolve_protocol(project_name, project_info)
        context["project_info"] = resolved

        # The canonical baseline is CoinGecko (already in `resolved`) plus the
        # DeFiLlama figures fetched here. TVL, fees and revenue are contested,
        # fast-moving and — unlike price — not something an agent gets handed by
        # a single obvious endpoint, so without a baseline every agent re-derives
        # them from web_search and lands wherever that minute's ranking lands.
        # Four small requests, ~20 KB, no API key.
        defi_facts = await fetch_canonical_defi_facts(project_name, resolved)
        if defi_facts.get("unavailable"):
            logger.info(
                "Canonical DeFiLlama baseline for %s incomplete: %s",
                project_name,
                defi_facts["unavailable"],
            )
        case_context = build_case_context(project_name, resolved, defi_facts)

        # Known cross-report contradictions, from the periodic consistency sweep.
        #
        # Without this the sweep's findings are queryable over HTTP and read by
        # nobody — an agent has no reason to go looking for a contradiction it
        # does not know exists. Rendered beside the canonical metrics, where
        # BaseAgent already surfaces case_context to every agent.
        #
        # Non-fatal by construction: a sweep that has never run, or a database
        # that is unreachable, yields an empty string and the evaluation proceeds
        # exactly as before. A warning system must not be able to stop the work
        # it is warning about.
        try:
            from app.knowledge.consistency import render_active_warnings

            warnings_text = await render_active_warnings()
            if warnings_text:
                case_context["known_contradictions"] = warnings_text
                logger.info(
                    "Surfaced %d chars of cross-report contradictions to the committee",
                    len(warnings_text),
                )
        except Exception as exc:
            logger.warning("Consistency warnings unavailable (non-fatal): %s", exc)

        context["case_context"] = case_context

        if on_status:
            await on_status("step", "gate_structural_check", {})
        gate = await run_structural_gate(resolved)
        if not gate.passed:
            if on_status:
                await on_status("gate_failed", "structural_check", {"failures": gate.blocking_failures})
            # api/evaluate.py used to discard this and write `completed`, so a
            # project the gate REJECTED was recorded as a finished evaluation
            # with no report. It now honours the field.
            return {
                "project_name": project_name,
                "status": STATUS_GATE_FAILED,
                "run_health": {
                    "report_usable": False,
                    "report_failure_reason": "gate_failed",
                    "agents_run": 0,
                    "agents_failed": 0,
                    "failed_agents": [],
                    "degraded_only_failures": [],
                    "data_agents_total": 0,
                    "data_agents_failed": [],
                    "score_weight_covered": None,
                    "risk_officer_ran": False,
                    "chair_decided": False,
                    "vetoed": False,
                },
                "gate_result": {
                    "passed": False,
                    "checks": gate.checks,
                    "blocking_failures": gate.blocking_failures,
                    "warnings": gate.warnings,
                },
                "agent_results": {},
                "scores": {},
                "overall_score": None,
                "recommendation": "FAIL_GATE",
            }

        # What this committee decided about this project last time, if anything.
        #
        # WHO SEES THIS, AND WHY ONLY THEM
        #
        # It goes into `prior_evaluation_context`, a key that ONLY
        # agents/report_writer.py reads. It is deliberately NOT merged into
        # `knowledge_context`, which is the channel BaseAgent renders under
        # RELEVANT PRIOR KNOWLEDGE: that string is shared by the whole run, so
        # using it would hand the previous decision and score to all eight data
        # agents and the Risk Officer at once. Three reasons not to:
        #
        # 1. Anchoring. The eight data agents exist to observe the world
        #    freshly. Telling tokenomics_analyst "the committee scored this 34.3
        #    and said PASS in June" invites it to reconcile with that number
        #    rather than measure. docs/CONTRACTS.md 4.2 makes their mutual
        #    independence the design; a shared prior conclusion is a different
        #    kind of correlation but it is still correlation.
        # 2. The retrospective locates the defect precisely: "The failure is at
        #    adjudication, not at collection" (02-findings.md F2). Memory
        #    belongs where the failure is.
        # 3. Cost. Fifteen agents times ~1,550 characters is ~5,800 input tokens
        #    a run for material fourteen of them cannot act on, while a parallel
        #    workstream is cutting input cost.
        #
        # The Chair still gets it, and gets it in the right form: section 25 of
        # the report, which chair.format_report_for_chair renders like any other
        # section. So the agent that decides whether the prior call still holds
        # reads the committee's own adjudicated answer to that question rather
        # than the raw ledger.
        #
        # `exclude_evaluation_id` keeps a re-run from matching itself.
        prior_evaluation = await self._load_prior_evaluation(
            project_name, resolved, evaluation_id
        )
        context["prior_evaluation_context"] = _render_prior_context(prior_evaluation)

        if on_status:
            await on_status("step", "1_intelligence_gathering", {"agents": [agent.name for agent in self.data_agents]})
        data_tasks = [self._run_agent(agent, context, on_status) for agent in self.data_agents]
        data_results = await asyncio.gather(*data_tasks, return_exceptions=True)
        for agent, result in zip(self.data_agents, data_results):
            if isinstance(result, Exception):
                agent_results[agent.name] = AgentResult(agent_name=agent.name, output={"error": str(result)}, error=str(result))
            else:
                agent_results[agent.name] = result

        prior = {name: result.output for name, result in agent_results.items() if not result.error}
        refresh_context()

        if on_status:
            await on_status("step", "data_reconciliation", {})
        data_layer_reconciliation = _reconcile(prior, case_context, "data_layer")
        context["reconciliation"] = data_layer_reconciliation

        if on_status:
            await on_status("step", "4_maturation_scoring", {})
        context["prior_agent_outputs"] = prior
        maturation = await self._run_agent(self.maturation, context, on_status)
        agent_results[self.maturation.name] = maturation
        prior[self.maturation.name] = maturation.output
        refresh_context()

        if on_status:
            await on_status("step", "5_risk_veto", {})
        context["prior_agent_outputs"] = prior
        risk = await self._run_agent(self.risk_officer, context, on_status)
        agent_results[self.risk_officer.name] = risk
        prior[self.risk_officer.name] = risk.output
        refresh_context()
        vetoed = risk.output.get("veto", False)
        veto_reason = risk.output.get("veto_reason", "")
        if vetoed and on_status:
            await on_status("veto", self.risk_officer.name, {"reason": veto_reason})

        if on_status:
            await on_status("step", "5b_devils_advocate", {})
        context["prior_agent_outputs"] = prior
        devil = await self._run_agent(self.devils_advocate, context, on_status)
        agent_results[self.devils_advocate.name] = devil
        prior[self.devils_advocate.name] = devil.output
        refresh_context()

        if on_status:
            await on_status("step", "6_portfolio_assessment", {})
        context["prior_agent_outputs"] = prior
        portfolio = await self._run_agent(self.portfolio_manager, context, on_status)
        agent_results[self.portfolio_manager.name] = portfolio
        prior[self.portfolio_manager.name] = portfolio.output
        refresh_context()

        if on_status:
            await on_status("step", "7_report_assembly", {})
        context["prior_agent_outputs"] = prior
        report = await self._run_agent(self.report_writer, context, on_status)
        agent_results[self.report_writer.name] = report
        draft_report = report.output
        refresh_context()

        # THE REPORT IS THE DELIVERABLE, AND ITS ABSENCE IS NOT A SUCCESS.
        #
        # Five of the sixteen persisted evaluations reached this line with no
        # `sections` and every one of them was recorded `status = completed`:
        # four ``Error code: 429 ... exceeded your current quota`` (Polkadot
        # 5e6e4f2d, Hyperliquid b028881a, Chainlink b22be475, Aave 0f48a034)
        # and one output-ceiling parse failure (Hyperliquid e2d96b62). Four
        # were re-run by hand the same day, so the user got a report — but only
        # because a human was watching. Nothing in the system said anything was
        # wrong.
        #
        # The fallback below is UNCHANGED. Ray and the Chair still run, exactly
        # as before, and the run still returns. What changes is that the result
        # now carries a status saying the artefact is missing, so the record
        # stops claiming otherwise.
        #
        # WHY THE CHAIR'S VERDICT ON THIS STUB CANNOT BE CALIBRATED. The Chair
        # reads `draft_report`, `ray_take`, `technical_entry_context` and the
        # source catalog, and nothing else — chair.py::get_system_prompt does
        # not touch `prior_agent_outputs`, so when the report is gone the
        # Chair's entire view of fifteen agents' work is the five keys below,
        # one of which is the literal string "Report incomplete". Whatever it
        # returns is not the committee's judgement of the project, and the
        # ledger must not acquire a row that looks like one. Measured: in all
        # five production cases the Chair produced no `decision` at all, so
        # today's CHAIR_FAILED branch already catches them — but it catches
        # them by accident, because the Chair happened to die too. The skip is
        # made explicit below on the report's own account.
        report_usable, report_failure_reason = report_deliverable_state(report)
        if not report_usable:
            logger.error(
                "REPORT WRITER PRODUCED NO REPORT for %s (%s): %s. Recording the "
                "run as %s. The remaining agent outputs are still persisted and "
                "the run is re-adjudicable.",
                project_name,
                report_failure_reason,
                (report.error or draft_report.get("parse_error") or "no sections key"),
                STATUS_REPORT_FAILED,
            )
            draft_report = {
                "summary": draft_report.get("summary", "Report incomplete"),
                "overall_score": self._calc_score(agent_results),
                "risk_score": risk.score,
                "recommendation": "VETO" if vetoed else self._simple_rec(
                    {name: result.score for name, result in agent_results.items() if result.score is not None}
                ),
                "footnotes": [],
            }

        # RECONCILIATION, SECOND PASS — the whole run, including the report.
        #
        # The first pass runs against `prior`: the eight data agents, before
        # synthesis and before the Report Writer exists. So the one agent that
        # assembles every other agent's figures into a single document — and
        # therefore the one agent that can contradict *itself* — was the only
        # agent nothing checked. On Aave evaluation c1479a94 three agents put
        # Aave's TVL at $25.7B and three others at $61.9B, both "across 20+
        # chains"; the Report Writer used the first figure in its executive
        # summary and the second in its project overview, and the Chair decided
        # on $25.7B without ever being told half the committee disagreed. The
        # first pass could not have seen it: it runs before the report exists.
        #
        # WHY HERE AND NOT LATER. Two agents still run after this point, and
        # both of them are adjudicators — Ray reviews the report, the Chair
        # decides on it. A contradiction found after the Chair has spoken is an
        # observation about a decision already made. Found here, it is an input
        # to that decision. Later would also be cheaper and useless, which is
        # what the first pass already was.
        #
        # WHY NOT INSTEAD OF THE FIRST PASS. The first pass costs no tokens and
        # answers a different question: did the data layer already disagree with
        # itself, or did synthesis introduce it? Both results are persisted
        # (`data_reconciliation` and `data_reconciliation_data_layer`), so that
        # question is answerable from the record rather than from a log line.
        # The Technical Analyst is excluded from this pass, and only from this
        # pass. CONTRACTS §4.1: it is in `exclude_from_scores` and reaches the
        # Chair *only* as `technical_entry_context`; a contradiction block that
        # named it as a source would be a second channel from that agent into
        # the adjudication, which is the thing §4.1 forbids. It is still
        # reconciled in the data-layer pass, which reaches no prompt.
        #
        # Measured cost of the exclusion on the GMX run: zero. Its one
        # extractable figure was "trading at $7.20", which `consistency.binding_is_sound`
        # already refuses as a share price mislabelled a daily volume.
        chair_visible = {
            name: output
            for name, output in prior.items()
            if name != TechnicalAnalyst.name
        }
        run_reconciliation = _reconcile(
            {**chair_visible, self.report_writer.name: draft_report},
            case_context,
            "full_run",
        )
        context["reconciliation"] = run_reconciliation
        # `data_reconciliation` keeps its name and now carries the superset —
        # the whole run, which is also what the Chair was shown.
        reconciliation = run_reconciliation

        # Where the finding goes, and why it goes there.
        #
        # `case_context` carries the *cross-report* contradictions to every data
        # agent, and BaseAgent renders it. That channel is unavailable here:
        # both remaining agents override `get_system_prompt` and neither reads
        # `case_context`, and in any case they run after it was assembled.
        #
        # `chair.format_report_for_chair` renders every non-`sections` key of
        # the report as labelled prompt text under REPORT METADATA, so attaching
        # the block to the report puts it in front of the adjudicator with no
        # change to a file this branch does not own. It is also persisted with
        # the report, which is where an audit record of "the committee
        # contradicted itself and was told so" belongs. No renderer reads it:
        # api/reports.py addresses sections by name.
        #
        # Empty string when the run is clean, and the key is then absent
        # entirely — a clean evaluation pays nothing and looks exactly as it did
        # before.
        contradiction_text = render_contradictions(run_reconciliation)
        if contradiction_text:
            draft_report["data_contradictions"] = contradiction_text
            logger.warning(
                "Within-run contradictions in %s: %d found, %d chars surfaced to the Chair",
                project_name,
                run_reconciliation.get("contradictions_found", 0),
                len(contradiction_text),
            )

        if on_status:
            await on_status("step", "post_ray_dalio", {})
        ray_context = dict(context)
        ray_context["draft_report"] = draft_report
        ray = await self._run_agent(self.ray, ray_context, on_status)
        agent_results[self.ray.name] = ray
        refresh_context()

        # The weighted score is computed HERE, before the Chair runs.
        #
        # It used to be computed after the Chair had already decided, which made
        # a score/decision contradiction literally undetectable: at the moment
        # the Chair was prompted the number it might contradict did not exist
        # yet (docs/adr/0002-score-chair-coherence.md).
        #
        # Moving it cannot change its value. `_calc_score` reads only the ten
        # names in its `weights` table and `scores` filters on
        # `exclude_from_scores`; `committee_chair` is in that exclusion set and
        # is absent from `weights`, so whether the Chair's result is present in
        # `agent_results` at the time of the call is irrelevant to both.
        #
        # D6 boundary: the score is NOT placed in `chair_context`. The Chair is
        # told nothing new, decides exactly what it decided before, and the
        # disagreement is recorded rather than prevented.
        exclude_from_scores = {"report_writer", "ray_dalio", "committee_chair", "technical_analyst"}
        scores = {
            name: result.score
            for name, result in agent_results.items()
            if result.score is not None and name not in exclude_from_scores
        }
        overall = self._calc_score(agent_results)

        if on_status:
            await on_status("step", "8_committee_decision", {})
        chair_context = dict(context)
        chair_context["draft_report"] = draft_report
        chair_context["ray_take"] = ray.output
        chair_context["risk_veto"] = vetoed
        chair_context["risk_veto_reason"] = veto_reason
        chair_context["technical_entry_context"] = prior.get("technical_analyst", {})
        chair = await self._run_agent(self.chair, chair_context, on_status)
        agent_results[self.chair.name] = chair

        # A parse failure is not a verdict.
        #
        # On the Hyperliquid run the Chair hit its output ceiling mid-JSON. The
        # object would not parse, `decision` was absent, and this fallback wrote
        # INSUFFICIENT_DATA into the calibration ledger for a run whose own
        # preamble read "the committee and Ray converge on PASS". That is
        # indistinguishable, forever after, from a genuine "we could not assess
        # this" — and the ledger is the one artefact the whole calibration loop
        # depends on.
        chair_failed = bool(chair.error) or "parse_error" in chair.output
        decision = chair.output.get("decision", "VETO" if vetoed else "INSUFFICIENT_DATA")
        if vetoed:
            decision = "VETO"
        elif chair_failed and "decision" not in chair.output:
            decision = "CHAIR_FAILED"
            logger.error(
                "Chair produced no usable decision for %s (%s) - recording as "
                "CHAIR_FAILED, not as a verdict. tokens_out=%s",
                project_name,
                chair.output.get("parse_error") or chair.error,
                chair.tokens_output,
            )

        data_quality = aggregate_data_quality(agent_results)
        if data_quality["verified_ratio"] is not None and data_quality["verified_ratio"] < 0.5:
            logger.warning(
                "DATA QUALITY for %s: only %.0f%% of %d claims were verified "
                "(%d unknown gaps); agent confidence distribution %s",
                project_name,
                100 * data_quality["verified_ratio"],
                data_quality["verified_claims"] + data_quality["inferred_claims"],
                data_quality["unknown_gap_count"],
                data_quality["confidence_distribution"],
            )

        chair_confidence = str(chair.output.get("conviction_level", "unknown") or "unknown")
        reconciliation_check = build_score_reconciliation(
            overall, draft_report, str(decision), chair_confidence, bool(vetoed)
        )
        if reconciliation_check["conflict"]:
            logger.warning(
                "SCORE RECONCILIATION for %s (divergence=%s contradiction=%s apparent=%s): %s",
                project_name,
                reconciliation_check["divergence"],
                reconciliation_check["contradiction"],
                reconciliation_check["apparent_contradiction"],
                reconciliation_check["detail"],
            )

        run_health = build_run_health(
            agent_results,
            report_usable=report_usable,
            report_failure_reason=report_failure_reason,
            decision=str(decision),
            vetoed=bool(vetoed),
        )
        if run_health["score_weight_covered"] is not None and run_health["score_weight_covered"] < 1.0:
            logger.warning(
                "DEGRADED RUN for %s: overall_score %s was computed over %.0f%% of "
                "the weight table (failed: %s). It is renormalised to look like a "
                "whole-committee number and is not one.",
                project_name,
                overall,
                100 * run_health["score_weight_covered"],
                run_health["failed_agents"] or "none",
            )
        if not run_health["risk_officer_ran"]:
            logger.error(
                "RISK OFFICER DID NOT ANSWER for %s. `vetoed` is read off its "
                "output, so this run recorded 'no veto' from an agent that never "
                "cleared anything.",
                project_name,
            )

        result = {
            "project_name": project_name,
            "status": STATUS_COMPLETED if report_usable else STATUS_REPORT_FAILED,
            # Instrument only — see build_run_health. Never affects `status`.
            "run_health": run_health,
            "case_time": case_context.get("case_time"),
            "data_reconciliation": reconciliation,
            "data_reconciliation_data_layer": data_layer_reconciliation,
            "gate_result": {"passed": True, "warnings": gate.warnings, "checks": gate.checks},
            "agent_results": {name: self._ser(result) for name, result in agent_results.items()},
            "scores": scores,
            "overall_score": overall,
            "risk_score": risk.score,
            "vetoed": vetoed,
            "veto_reason": veto_reason,
            "recommendation": decision,
            "chair_reasoning": chair.output.get("reasoning", ""),
            "ray_verdict": ray.output.get("rays_verdict", ""),
            "draft_report": draft_report,
            "signposts": chair.output.get("signposts", []),
            "review_date": chair.output.get("review_date", ""),
            # Written verbatim into reports.content by api/evaluate._persist_report,
            # so the rates are queryable rather than reconstructed months later:
            #   SELECT content->'score_reconciliation'->>'divergence'     AS diverged,
            #          content->'score_reconciliation'->>'contradiction'  AS contradicted,
            #          count(*)
            #     FROM reports GROUP BY 1, 2;
            "score_reconciliation": reconciliation_check,
            # Aggregated, persisted, and deliberately not fed back into any
            # agent's `confidence` — see aggregate_data_quality.
            "data_quality": data_quality,
        }

        # Absent entirely on a first-time evaluation, so nothing downstream can
        # tell the difference between this pipeline and the one before it.
        prior_summary = self._prior_summary(prior_evaluation)
        if prior_summary is not None:
            result["prior_evaluation"] = prior_summary

        project_metadata = context.get("project_info", {})
        await self._notion_write(
            project_name, project_metadata, agent_results, overall, decision, evaluation_id
        )

        try:
            from app.knowledge.calibration import record_calibration

            if not report_usable:
                # See the long note at the Report Writer call site. The Chair
                # adjudicated on a five-key stub, not on the committee's work.
                raise _SkipCalibration

            if decision == "CHAIR_FAILED":
                # Nothing to calibrate. A run whose adjudication failed has no
                # recommendation to grade against a future price, and writing one
                # anyway is how the ledger acquires rows that look like verdicts
                # and are not. The evaluation, its agent outputs and its report
                # are all still persisted, so the run is recoverable and can be
                # re-adjudicated; it simply does not enter the scorecard.
                raise _SkipCalibration

            if evaluation_id is None:
                logger.warning(
                    "Calibration record for %s will be orphaned: no evaluation_id "
                    "was supplied to Orchestrator.evaluate()",
                    project_name,
                )

            record_id = await record_calibration(
                evaluation_id=evaluation_id,
                project_name=project_name,
                ticker=str(project_metadata.get("ticker", "") or ""),
                coingecko_id=str(project_metadata.get("coingecko_id", "") or ""),
                category=str(project_metadata.get("category", "") or ""),
                recommendation=decision,
                overall_score=overall,
                chair_confidence=chair_confidence,
                vetoed=bool(vetoed),
            )

            # The Chair's falsification criteria. record_calibration's signature
            # is frozen (docs/CONTRACTS.md 3.1), so these land as a follow-up
            # update against the row it just created. Without them a WATCH is
            # unfalsifiable: the committee states what would change its mind and
            # the ledger discards it, which is most of why four of six live
            # records cannot be graded.
            if record_id:
                from app.knowledge.calibration import record_signposts

                signposts = result.get("signposts") or []
                review_date = result.get("review_date") or None
                if signposts or review_date:
                    await record_signposts(
                        record_id=record_id,
                        signposts=[str(s) for s in signposts] if signposts else None,
                        review_date=str(review_date) if review_date else None,
                    )
        except _SkipCalibration:
            logger.warning(
                "Calibration skipped for %s: %s, so there is no verdict to grade. "
                "Evaluation and agent outputs are still persisted.",
                project_name,
                "the Report Writer produced no report and the Chair decided on a stub"
                if not report_usable
                else "the Chair produced no usable decision",
            )
        except Exception as exc:
            logger.warning("Calibration capture failed (non-fatal): %s", exc)

        return result

    async def _load_prior_evaluation(
        self, project_name: str, resolved: JSONObject, evaluation_id: str | None
    ):
        """The previous evaluation of this project, or None. Never raises.

        A memory lookup must not be able to fail a 15-agent run, so every
        failure path here degrades to "no prior" — which is exactly the
        behaviour the pipeline had before this existed.
        """
        try:
            from app.knowledge.history import get_prior_evaluation

            prior = await get_prior_evaluation(
                project_name,
                str(resolved.get("coingecko_id", "") or "") or None,
                exclude_evaluation_id=evaluation_id,
            )
        except Exception as exc:
            logger.warning("Prior-evaluation lookup failed for %s: %s", project_name, exc)
            return None

        if prior is None:
            logger.info("No prior evaluation found for %s - first-time run", project_name)
        else:
            logger.info(
                "Prior evaluation for %s: %s on %s (%s days ago), decision=%s "
                "score=%s source=%s usable=%s matched_by=%s",
                project_name,
                prior.evaluation_id,
                prior.evaluated_at,
                prior.days_since,
                prior.decision,
                prior.overall_score,
                prior.source,
                prior.usable,
                prior.matched_by,
            )
        return prior

    def _prior_summary(self, prior) -> JSONObject | None:
        """A compact, JSON-safe record of which prior this run was compared to.

        Written into the result and therefore into `reports.content`, so a later
        reader can tell which earlier evaluation section 25 was measured
        against instead of having to re-derive it:

            SELECT content->'prior_evaluation'->>'evaluation_id' FROM reports;
        """
        if prior is None:
            return None
        outcome = prior.outcome
        return {
            "evaluation_id": prior.evaluation_id,
            "evaluated_at": prior.evaluated_at.isoformat() if prior.evaluated_at else None,
            "days_since": prior.days_since,
            "matched_by": prior.matched_by,
            "source": prior.source,
            "usable": prior.usable,
            "unusable_reason": prior.unusable_reason,
            "decision": prior.decision,
            "overall_score": prior.overall_score,
            "signpost_count": len(prior.signposts),
            "signposts_source": prior.signposts_source,
            "review_date": prior.review_date.isoformat() if prior.review_date else None,
            "review_date_passed": prior.review_date_passed,
            "calibration_record_id": outcome.record_id if outcome else None,
            "graded_horizons": outcome.graded_horizons if outcome else [],
            "skipped_unusable": prior.skipped_unusable,
        }

    async def _resolve_protocol(self, name: str, info: JSONObject) -> JSONObject:
        from app.tools import get_tool_registry

        registry = get_tool_registry()
        resolved = dict(info)
        resolved["project_name"] = name
        cg_id = info.get("coingecko_id", "")

        if not cg_id:
            resolved["_resolution_search"] = await registry.execute("web_search", {"query": f"{name} crypto coingecko"})

        if cg_id:
            resolved["_price_data"] = await registry.execute("get_price", {"coin_id": cg_id})
            resolved["_token_data"] = await registry.execute("get_token_info", {"coin_id": cg_id})

        return resolved

    async def _run_agent(self, agent: BaseAgent, ctx: JSONObject, cb: StatusCallback = None) -> AgentResult:
        if cb:
            await cb("agent_start", agent.name, {})
        result = await agent.run(ctx)
        if cb:
            await cb(
                "agent_complete",
                agent.name,
                {
                    "score": result.score,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                    "tokens": result.tokens_input + result.tokens_output,
                },
            )
        return result

    def _calc_score(self, results: dict[str, AgentResult]) -> float | None:
        weights = SCORE_WEIGHTS
        total_weight = 0.0
        weighted_sum = 0.0
        for name, weight in weights.items():
            result = results.get(name)
            if result and result.score is not None:
                weighted_sum += result.score * weight
                total_weight += weight
        return round(weighted_sum / total_weight, 1) if total_weight > 0 else None

    def _simple_rec(self, scores: dict[str, float]) -> str:
        """Fallback recommendation used only when the Report Writer emitted no
        `sections` key. Unweighted mean over a different agent set than
        `_calc_score`'s weighted one — deliberately left as it was; only the
        hardcoded 75/60 literals were replaced by the shared constants."""
        if not scores:
            return "INSUFFICIENT_DATA"
        band = score_band(sum(scores.values()) / len(scores))
        return _BAND_DECISION[band] if band else "INSUFFICIENT_DATA"

    def _ser(self, result: AgentResult) -> JSONObject:
        return {
            "agent_name": result.agent_name,
            "output": result.output,
            "score": result.score,
            "model_used": result.model_used,
            "tokens_input": result.tokens_input,
            "tokens_output": result.tokens_output,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "tool_calls_made": result.tool_calls_made,
            "sources": result.sources,
        }

    # Recommendation -> callout styling. Notion colours are fixed names, and the
    # decision is the one thing a reader should register before reading anything
    # else, so it gets the colour rather than the prose.
    _NOTION_DECISION_STYLE: dict[str, tuple[str, str]] = {
        "INVEST": ("🟢", "green_background"),
        "BUY": ("🟢", "green_background"),
        "WATCH": ("🟡", "yellow_background"),
        "PASS": ("🔴", "red_background"),
        "VETO": ("⛔", "red_background"),
        "INSUFFICIENT_DATA": ("⚪", "gray_background"),
    }

    # Agents whose output is the decision itself or a rendering of it, rather
    # than a finding. The Chair is pulled out separately below.
    _NOTION_SKIP_AGENTS = frozenset({"report_writer", "ray_dalio", "committee_chair"})

    def _notion_blocks(self, name, info, results, score, rec, evaluation_id=None):
        """Build the Notion page body for one evaluation run as real blocks.

        Everything here used to be one flat markdown string dropped into
        paragraph blocks, which is why the live pages read as walls of text with
        literal `**` in them. The shape is: a colour-coded decision callout,
        links out to the full report, the Chair's reasoning, the convergence-
        ranked risks as an actual bulleted list, then one collapsible section
        per agent. A divider leads each run because the writer appends on every
        re-evaluation and the runs otherwise ran together.
        """
        from datetime import datetime, timezone

        from app.tools.notion import (
            bullet_blocks,
            callout_block,
            divider_block,
            heading_block,
            paragraph_blocks,
            resolve_report_base,
            rich_bullet_block,
            rich_paragraph_block,
            rich_text,
            quote_blocks,
            toggle_block,
        )

        emoji, colour = self._NOTION_DECISION_STYLE.get(
            str(rec).upper(), ("⚪", "gray_background")
        )
        chair = results.get("committee_chair")
        chair_output = chair.output if chair and isinstance(chair.output, dict) else {}
        conviction = str(chair_output.get("conviction_level", "") or "")

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        blocks: list[dict] = [
            divider_block(),
            heading_block(f"Evaluation — {stamp}", 1),
        ]

        headline = rich_text(str(rec or "NO DECISION"), bold=True)
        score_text = "n/a" if score is None else f"{float(score):g}"
        detail = f"  ·  score {score_text}/100"
        if conviction:
            detail += f"  ·  conviction: {conviction}"
        ticker = str(info.get("ticker", "") or "")
        if ticker:
            detail += f"  ·  {ticker.upper()}"
        headline.extend(rich_text(detail))
        blocks.append(callout_block(headline, emoji=emoji, color=colour))

        # Full report links. The committee's detailed output lives behind the
        # reports API; the page is the index into it, not a replacement for it.
        if evaluation_id:
            base = resolve_report_base()
            link_line = rich_text("Full report: ")
            link_line.extend(
                rich_text("HTML", bold=True, link=f"{base}/api/reports/{evaluation_id}/html")
            )
            link_line.extend(rich_text("  ·  "))
            link_line.extend(
                rich_text("Markdown", bold=True, link=f"{base}/api/reports/{evaluation_id}/markdown")
            )
            blocks.extend(rich_paragraph_block(link_line))
            # The id is written as plain text as well: if the host in the links
            # above ever stops resolving, the evaluation is still recoverable
            # from the id alone, which is the failure mode CONTRACTS 2.5 records.
            id_line = rich_text("evaluation id: ")
            id_line.extend(rich_text(str(evaluation_id), code=True))
            blocks.extend(rich_paragraph_block(id_line))

        chair_reasoning = str(chair_output.get("reasoning", "") or "")
        chair_summary = str(chair_output.get("summary", "") or "")
        if chair_summary or chair_reasoning:
            blocks.append(heading_block("Chair's verdict", 2))
            blocks.extend(quote_blocks(chair_summary or chair_reasoning))
            if chair_reasoning and chair_summary:
                blocks.extend(paragraph_blocks(chair_reasoning))

        # Convergence-ranked risks, as a real list. dedupe_risks ranks by the
        # number of distinct agents that named each risk; that count is the
        # strongest signal this committee produces, so it is stated in bold
        # rather than left implicit in an attribution line.
        risks = dedupe_risks(results, set(self._NOTION_SKIP_AGENTS))
        if risks:
            blocks.append(heading_block("Key risks", 2))
            for risk in risks:
                count = int(risk["agent_count"])
                agents = ", ".join(risk["agents"])
                line = []
                if count > 1:
                    line.extend(
                        rich_text(f"[{count} agents] ", bold=True, color="red")
                    )
                line.extend(rich_text(str(risk["text"])))
                line.extend(rich_text(f"  — {agents}", italic=True, color="gray"))
                blocks.extend(rich_bullet_block(line))

        # One collapsible section per agent, name in real bold, score alongside.
        findings: list[dict] = []
        for agent_name, result in results.items():
            if agent_name in self._NOTION_SKIP_AGENTS:
                continue
            if result.error or not isinstance(result.output, dict):
                continue
            summary = str(result.output.get("summary", "") or "")
            key_findings = result.output.get("key_findings")
            agent_risks = result.output.get("risks")
            if not summary and not key_findings:
                continue

            # The raw agent_name, not a prettified form: it is the key in
            # agent_outputs, and this page has outlived that table before.
            label = rich_text(agent_name, bold=True)
            score_text = "n/a" if result.score is None else f"{float(result.score):g}"
            label.extend(rich_text(f"  —  score {score_text}", color="gray"))
            confidence = str(result.output.get("confidence", "") or "")
            if confidence:
                label.extend(rich_text(f"  ·  confidence {confidence}", italic=True, color="gray"))

            def listing(items, title, limit=12):
                """Bullets for one of an agent's lists.

                A cap is needed — a toggle's children share the parent block and
                cannot spill into a follow-up append — but an overrun is stated
                in the page rather than the list just stopping. The full report
                is linked at the top of every run, so nothing is unrecoverable.
                """
                if not isinstance(items, list) or not items:
                    return []
                out = [heading_block(title, 3)]
                for entry in items[:limit]:
                    out.extend(bullet_blocks(str(entry)))
                overflow = len(items) - limit
                if overflow > 0:
                    out.extend(
                        rich_bullet_block(
                            rich_text(
                                f"+{overflow} more — see the full report linked above",
                                italic=True,
                                color="gray",
                            )
                        )
                    )
                return out

            children: list[dict] = []
            if summary:
                children.extend(paragraph_blocks(summary))
            children.extend(listing(key_findings, "Key findings"))
            children.extend(listing(agent_risks, "Risks"))

            findings.append(toggle_block(label, children))

        if findings:
            blocks.append(heading_block("Agent findings", 2))
            blocks.extend(findings)

        return blocks

    async def _notion_write(self, name, info, results, score, rec, evaluation_id=None):
        from app.config import get_settings

        settings = get_settings()
        if not settings.notion_api_key or not settings.notion_projects_db:
            return
        # Deliberately swallowing: a 15-agent run that succeeded must not be
        # lost because a block append failed.
        try:
            from app.tools.notion import update_project_evaluation

            await update_project_evaluation(
                project_name=name,
                ticker=info.get("ticker", ""),
                category=info.get("category", ""),
                score=score,
                recommendation=rec,
                report_blocks=self._notion_blocks(
                    name, info, results, score, rec, evaluation_id
                ),
            )
        except Exception as exc:
            logger.warning("Notion writeback failed: %s", exc)
