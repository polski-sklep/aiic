"""9-Step Committee Orchestrator Pipeline.

Step 0: Protocol Resolution — resolve project identity, fetch baseline data
Step 1: Intelligence Gathering — 7 data agents run in parallel
 GATE:  Structural Check — hard limits, mandate exclusions
Step 4: Maturation Scoring — growth stage assessment
Step 5: Risk + Stress — Risk Officer with VETO POWER
Step 5b: Devil's Advocate — contrarian challenge
Step 6: Portfolio Assessment — fit, sizing, correlation
Step 7: Report + Thesis Assembly — 24-section structured report
Post:  Ray Munger — independent contrarian Sonnet pass
Step 8: Committee Decision — Chair makes final BUY/PASS/WATCH/VETO
"""
from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from app.agents.base import BaseAgent, AgentResult
from app.agents.tokenomics import TokenomicsAnalyst
from app.agents.data_agents import (
    GovernanceAnalyst, OnChainAnalyst, TechInfraAnalyst,
    CompetitiveIntel, FieldIntel, LegalRegulatory,
)
from app.agents.risk_officer import RiskOfficer
from app.agents.synthesis_agents import MaturationScorer, DevilsAdvocate, PortfolioManager
from app.agents.report_writer import ReportWriter
from app.agents.ray import RayDalio
from app.agents.chair import CommitteeChair
from app.agents.guardrails import run_structural_gate
from app.agents.reconciliation import build_case_context, reconcile_data
from app.utils.citations import build_source_catalog
from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, str, JSONObject], Awaitable[None]] | None


class Orchestrator:
    """9-step committee evaluation pipeline with 14 agents."""

    def __init__(self):
        # Step 1: Parallel data-gathering (Sonnet)
        self.data_agents: list[BaseAgent] = [
            TokenomicsAnalyst(),
            GovernanceAnalyst(),
            OnChainAnalyst(),
            TechInfraAnalyst(),
            CompetitiveIntel(),
            FieldIntel(),
            LegalRegulatory(),
        ]
        # Steps 4-6: Sequential synthesis (Opus)
        self.maturation = MaturationScorer()
        self.devils_advocate = DevilsAdvocate()
        self.risk_officer = RiskOfficer()
        self.portfolio_manager = PortfolioManager()
        # Step 7: Report
        self.report_writer = ReportWriter()
        # Step 8: Decision
        self.chair = CommitteeChair()
        # Post-processing
        self.ray = RayDalio()

    async def evaluate(
        self,
        project_name: str,
        project_info: JSONObject | None = None,
        knowledge_context: str = "",
        current_portfolio: list[JSONObject] | None = None,
        on_status: StatusCallback = None,
    ) -> JSONObject:
        """Run full 9-step evaluation pipeline."""
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

        # === STEP 0: Protocol Resolution ===
        if on_status:
            await on_status("step", "0_protocol_resolution", {})
        resolved = await self._resolve_protocol(project_name, project_info)
        context["project_info"] = resolved

        # === Build case context ===
        case_context = build_case_context(project_name, resolved)
        context["case_context"] = case_context

        # === GATE: Structural Check ===
        if on_status:
            await on_status("step", "gate_structural_check", {})
        gate = await run_structural_gate(resolved)
        if not gate.passed:
            if on_status:
                await on_status("gate_failed", "structural_check", {
                    "failures": gate.blocking_failures
                })
            return {
                "project_name": project_name,
                "status": "gate_failed",
                "gate_result": {"passed": False, "checks": gate.checks,
                                "blocking_failures": gate.blocking_failures,
                                "warnings": gate.warnings},
                "agent_results": {}, "scores": {},
                "overall_score": None, "recommendation": "FAIL_GATE",
            }

        # === STEP 1: Intelligence Gathering (parallel) ===
        if on_status:
            await on_status("step", "1_intelligence_gathering", {
                "agents": [a.name for a in self.data_agents]
            })
        data_tasks = [self._run_agent(a, context, on_status) for a in self.data_agents]
        data_results = await asyncio.gather(*data_tasks, return_exceptions=True)
        for agent, result in zip(self.data_agents, data_results):
            if isinstance(result, Exception):
                agent_results[agent.name] = AgentResult(
                    agent_name=agent.name, output={"error": str(result)}, error=str(result))
            else:
                agent_results[agent.name] = result

        prior = {n: r.output for n, r in agent_results.items() if not r.error}
        refresh_context()

        # === DATA RECONCILIATION ===
        if on_status:
            await on_status("step", "data_reconciliation", {})
        reconciliation = reconcile_data(prior, case_context)
        context["reconciliation"] = reconciliation
        if reconciliation.get("inconsistencies_found", 0) > 0:
            logger.warning("Data reconciliation: %d inconsistencies" % reconciliation["inconsistencies_found"])

        # === STEP 4: Maturation Scoring ===
        if on_status:
            await on_status("step", "4_maturation_scoring", {})
        context["prior_agent_outputs"] = prior
        mat = await self._run_agent(self.maturation, context, on_status)
        agent_results[self.maturation.name] = mat
        prior[self.maturation.name] = mat.output
        refresh_context()

        # === STEP 5: Risk + Stress (VETO POWER) ===
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

        # === STEP 5b: Devil's Advocate ===
        if on_status:
            await on_status("step", "5b_devils_advocate", {})
        context["prior_agent_outputs"] = prior
        devils = await self._run_agent(self.devils_advocate, context, on_status)
        agent_results[self.devils_advocate.name] = devils
        prior[self.devils_advocate.name] = devils.output
        refresh_context()

        # === STEP 6: Portfolio Assessment ===
        if on_status:
            await on_status("step", "6_portfolio_assessment", {})
        context["prior_agent_outputs"] = prior
        port = await self._run_agent(self.portfolio_manager, context, on_status)
        agent_results[self.portfolio_manager.name] = port
        prior[self.portfolio_manager.name] = port.output
        refresh_context()

        # === STEP 7: Report Assembly ===
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
                    {n: r.score for n, r in agent_results.items() if r.score is not None}),
                "footnotes": [],
            }

        # === POST: Ray Munger ===
        if on_status:
            await on_status("step", "post_ray_dalio", {})
        c_ctx = dict(context)
        c_ctx["draft_report"] = draft_report
        ray = await self._run_agent(self.ray, c_ctx, on_status)
        agent_results[self.ray.name] = ray
        refresh_context()

        # === STEP 8: Committee Decision ===
        if on_status:
            await on_status("step", "8_committee_decision", {})
        ch_ctx = dict(context)
        ch_ctx["draft_report"] = draft_report
        ch_ctx["ray_take"] = ray.output
        ch_ctx["risk_veto"] = vetoed
        ch_ctx["risk_veto_reason"] = veto_reason
        chair = await self._run_agent(self.chair, ch_ctx, on_status)
        agent_results[self.chair.name] = chair

        # === FINAL OUTPUT ===
        exclude_from_scores = {"report_writer", "ray_dalio", "committee_chair"}
        scores = {n: r.score for n, r in agent_results.items() if r.score is not None and n not in exclude_from_scores}
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
            "agent_results": {n: self._ser(r) for n, r in agent_results.items()},
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

        # Notion writeback
        await self._notion_write(project_name, context.get("project_info", {}),
                                  agent_results, overall, decision)
        return result

    # ------------------------------------------------------------------
    # Step 0
    # ------------------------------------------------------------------
    async def _resolve_protocol(self, name: str, info: JSONObject) -> JSONObject:
        from app.tools import get_tool_registry
        registry = get_tool_registry()
        resolved = dict(info)
        resolved["project_name"] = name
        cg_id = info.get("coingecko_id", "")

        if not cg_id:
            sr = await registry.execute("web_search", {"query": f"{name} crypto coingecko"})
            resolved["_resolution_search"] = sr

        if cg_id:
            resolved["_price_data"] = await registry.execute("get_price", {"coin_id": cg_id})
            resolved["_token_data"] = await registry.execute("get_token_info", {"coin_id": cg_id})

        return resolved

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _run_agent(self, agent: BaseAgent, ctx: JSONObject, cb: StatusCallback = None) -> AgentResult:
        if cb:
            await cb("agent_start", agent.name, {})
        result = await agent.run(ctx)
        if cb:
            await cb("agent_complete", agent.name, {
                "score": result.score, "error": result.error,
                "latency_ms": result.latency_ms,
                "tokens": result.tokens_input + result.tokens_output,
            })
        return result

    def _calc_score(self, results: dict[str, AgentResult]) -> float | None:
        w = {
            "tokenomics_analyst": 0.15, "onchain_analyst": 0.12,
            "tech_infra_analyst": 0.15, "governance_analyst": 0.08,
            "competitive_intel": 0.10, "field_intel": 0.05,
            "risk_officer": 0.15, "maturation_scorer": 0.10,
            "legal_regulatory": 0.05, "portfolio_manager": 0.05,
        }
        tw, ws = 0, 0
        for n, wt in w.items():
            r = results.get(n)
            if r and r.score is not None:
                ws += r.score * wt
                tw += wt
        return round(ws / tw, 1) if tw > 0 else None

    def _simple_rec(self, scores: dict[str, float]) -> str:
        if not scores:
            return "INSUFFICIENT_DATA"
        avg = sum(scores.values()) / len(scores)
        return "BUY" if avg >= 75 else ("WATCH" if avg >= 60 else "PASS")

    def _ser(self, r: AgentResult) -> JSONObject:
        return {
            "agent_name": r.agent_name, "output": r.output, "score": r.score,
            "model_used": r.model_used, "tokens_input": r.tokens_input,
            "tokens_output": r.tokens_output, "latency_ms": r.latency_ms,
            "error": r.error, "tool_calls_made": r.tool_calls_made,
            "sources": r.sources,
        }

    async def _notion_write(self, name, info, results, score, rec):
        from app.config import get_settings
        s = get_settings()
        if not s.notion_api_key:
            return
        try:
            from app.tools.notion import update_project_evaluation
            sums = []
            all_risks = []
            skip_agents = {"report_writer", "ray_dalio", "ray_dalio", "committee_chair"}
            for n, r in results.items():
                if n in skip_agents:
                    continue
                if r.output and not r.error:
                    sm = r.output.get("summary", "")
                    if sm:
                        sums.append(f"**{n}** (score: {r.score}): {sm}")
                    for risk in r.output.get("risks", []):
                        all_risks.append(f"[{n}] {risk}")
            seen = set()
            unique_risks = []
            for risk in all_risks:
                key = risk[:50].lower()
                if key not in seen:
                    seen.add(key)
                    unique_risks.append(risk)
            report_text = "\n\n".join(sums)
            if unique_risks[:5]:
                report_text += "\n\n---\n\n**Key Risks:**\n"
                for i, risk in enumerate(unique_risks[:5], 1):
                    report_text += f"\n{i}. {risk}"
            if s.notion_projects_db:
                await update_project_evaluation(
                    project_name=name, ticker=info.get("ticker", ""),
                    category=info.get("category", ""), score=score,
                    recommendation=rec, report_summary=report_text)
        except Exception as e:
            logger.warning(f"Notion writeback failed: {e}")
