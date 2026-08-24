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


def score_band(score: float | None) -> str | None:
    """Which recommendation band a weighted score falls in, or None."""
    if score is None:
        return None
    if score >= INVEST_SCORE_THRESHOLD:
        return "INVEST"
    if score >= WATCH_SCORE_THRESHOLD:
        return "WATCH"
    return "PASS"


def build_score_reconciliation(
    overall: float | None,
    decision: str,
    chair_confidence: str,
    vetoed: bool,
) -> ScoreReconciliation:
    """Compare the weighted score's band against the Chair's decision.

    Pure and side-effect free. It records the disagreement; it does not resolve
    it. Nothing here feeds back into `decision`, into `_calc_score`, or into any
    prompt — per PROJECT_DECISIONS.md D6 the choice of what to *do* about the
    incoherence is Jacob's, and this function exists to make the rate of it
    countable so that choice can be made on evidence rather than on the single
    Aave row (docs/adr/0002-score-chair-coherence.md).
    """
    band = score_band(overall)
    implied = _BAND_DECISION.get(band) if band else None
    thresholds = {"invest": INVEST_SCORE_THRESHOLD, "watch": WATCH_SCORE_THRESHOLD}

    if overall is None:
        comparable, conflict = False, False
        detail = "No weighted score was computable, so there is nothing to compare."
    elif vetoed:
        comparable, conflict = False, False
        detail = (
            f"Risk Officer veto overrides the Chair, so the {band} band "
            f"({overall}) is not comparable against the recorded VETO."
        )
    elif decision not in _COMPARABLE_DECISIONS:
        comparable, conflict = False, False
        detail = (
            f"Decision {decision!r} is not one of BUY/WATCH/PASS, so the "
            f"{band} band ({overall}) has nothing to disagree with."
        )
    else:
        comparable = True
        conflict = implied != decision
        if conflict:
            detail = (
                f"Weighted score {overall} falls in the {band} band, which "
                f"implies {implied}, but the Chair returned {decision} at "
                f"{chair_confidence} conviction."
            )
        else:
            detail = (
                f"Weighted score {overall} falls in the {band} band and the "
                f"Chair returned {decision}; they agree."
            )

    return {
        "overall_score": overall,
        "score_band": band,
        "band_implied_decision": implied,
        "chair_decision": decision,
        "chair_confidence": chair_confidence,
        "comparable": comparable,
        "conflict": conflict,
        "detail": detail,
        "thresholds": thresholds,
    }



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

        chair_confidence = str(chair.output.get("conviction_level", "unknown") or "unknown")
        reconciliation_check = build_score_reconciliation(
            overall, str(decision), chair_confidence, bool(vetoed)
        )
        if reconciliation_check["conflict"]:
            logger.warning("SCORE/DECISION CONFLICT for %s: %s", project_name, reconciliation_check["detail"])

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
            # so the conflict rate is queryable:
            #   SELECT content->'score_reconciliation'->>'conflict', count(*)
            #     FROM reports GROUP BY 1;
            "score_reconciliation": reconciliation_check,
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
            all_risks = []
            skip_agents = {"report_writer", "ray_dalio", "committee_chair"}
            for agent_name, result in results.items():
                if agent_name in skip_agents:
                    continue
                if result.output and not result.error:
                    summary = result.output.get("summary", "")
                    if summary:
                        summaries.append(f"**{agent_name}** (score: {result.score}): {summary}")
                    for risk in result.output.get("risks", []):
                        all_risks.append(f"[{agent_name}] {risk}")
            seen = set()
            unique_risks = []
            for risk in all_risks:
                key = risk[:50].lower()
                if key not in seen:
                    seen.add(key)
                    unique_risks.append(risk)
            report_text = "\n\n".join(summaries)
            if unique_risks[:5]:
                report_text += "\n\n---\n\n**Key Risks:**\n"
                for index, risk in enumerate(unique_risks[:5], start=1):
                    report_text += f"\n{index}. {risk}"
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
