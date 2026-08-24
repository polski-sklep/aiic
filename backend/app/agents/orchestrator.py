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
from app.agents.reconciliation import build_case_context, reconcile_data
from app.agents.report_writer import ReportWriter
from app.agents.risk_officer import RiskOfficer
from app.agents.synthesis_agents import DevilsAdvocate, MaturationScorer, PortfolioManager
from app.agents.technical_analyst import TechnicalAnalyst
from app.agents.tokenomics import TokenomicsAnalyst
from app.utils.citations import build_source_catalog
from app.utils.types import JSONObject, ScoreReconciliation

logger = logging.getLogger(__name__)

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

        case_context = build_case_context(project_name, resolved)
        context["case_context"] = case_context

        if on_status:
            await on_status("step", "gate_structural_check", {})
        gate = await run_structural_gate(resolved)
        if not gate.passed:
            if on_status:
                await on_status("gate_failed", "structural_check", {"failures": gate.blocking_failures})
            return {
                "project_name": project_name,
                "status": "gate_failed",
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
        reconciliation = reconcile_data(prior, case_context)
        context["reconciliation"] = reconciliation
        if reconciliation.get("inconsistencies_found", 0) > 0:
            logger.warning("Data reconciliation: %d inconsistencies", reconciliation["inconsistencies_found"])

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
        if "sections" not in draft_report:
            draft_report = {
                "summary": draft_report.get("summary", "Report incomplete"),
                "overall_score": self._calc_score(agent_results),
                "risk_score": risk.score,
                "recommendation": "VETO" if vetoed else self._simple_rec(
                    {name: result.score for name, result in agent_results.items() if result.score is not None}
                ),
                "footnotes": [],
            }

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

        decision = chair.output.get("decision", "VETO" if vetoed else "INSUFFICIENT_DATA")
        if vetoed:
            decision = "VETO"

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

        result = {
            "project_name": project_name,
            "status": "completed",
            "case_time": case_context.get("case_time"),
            "data_reconciliation": reconciliation,
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

        project_metadata = context.get("project_info", {})
        await self._notion_write(project_name, project_metadata, agent_results, overall, decision)

        try:
            from app.knowledge.calibration import record_calibration

            if evaluation_id is None:
                logger.warning(
                    "Calibration record for %s will be orphaned: no evaluation_id "
                    "was supplied to Orchestrator.evaluate()",
                    project_name,
                )

            await record_calibration(
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
        except Exception as exc:
            logger.warning("Calibration capture failed (non-fatal): %s", exc)

        return result

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
        weights = {
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

    async def _notion_write(self, name, info, results, score, rec):
        from app.config import get_settings

        settings = get_settings()
        if not settings.notion_api_key:
            return
        try:
            from app.tools.notion import update_project_evaluation

            summaries = []
            skip_agents = {"report_writer", "ray_dalio", "committee_chair"}
            for agent_name, result in results.items():
                if agent_name in skip_agents:
                    continue
                if result.output and not result.error:
                    summary = result.output.get("summary", "")
                    if summary:
                        summaries.append(f"**{agent_name}** (score: {result.score}): {summary}")

            report_text = "\n\n".join(summaries)
            report_text += format_risk_block(dedupe_risks(results, skip_agents))
            if settings.notion_projects_db:
                await update_project_evaluation(
                    project_name=name,
                    ticker=info.get("ticker", ""),
                    category=info.get("category", ""),
                    score=score,
                    recommendation=rec,
                    report_summary=report_text,
                )
        except Exception as exc:
            logger.warning("Notion writeback failed: %s", exc)
