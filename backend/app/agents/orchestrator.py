"""9-Step Committee Orchestrator Pipeline.

Step 0: Protocol Resolution - resolve project identity, fetch baseline data
Step 1: Intelligence Gathering - 8 data agents run in parallel
 GATE:  Structural Check - hard limits, mandate exclusions
Step 4: Maturation Scoring - growth stage assessment
Step 5: Risk + Stress - Risk Officer with VETO POWER
Step 5b: Devil's Advocate - contrarian challenge
Step 6: Portfolio Assessment - fit, sizing, correlation
Step 7: Report + Thesis Assembly - 24-section structured report
Post:  Ray Munger - independent contrarian Sonnet pass
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
from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, str, JSONObject], Awaitable[None]] | None


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

        exclude_from_scores = {"report_writer", "ray_dalio", "committee_chair", "technical_analyst"}
        scores = {
            name: result.score
            for name, result in agent_results.items()
            if result.score is not None and name not in exclude_from_scores
        }
        overall = self._calc_score(agent_results)
        decision = chair.output.get("decision", "VETO" if vetoed else "INSUFFICIENT_DATA")
        if vetoed:
            decision = "VETO"

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
                chair_confidence=str(chair.output.get("conviction_level", "unknown") or "unknown"),
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
        if not scores:
            return "INSUFFICIENT_DATA"
        average = sum(scores.values()) / len(scores)
        return "BUY" if average >= 75 else ("WATCH" if average >= 60 else "PASS")

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
