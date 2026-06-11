from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Evaluation, AgentOutput, Project
from app.utils.citations import normalize_footnotes, reindex_citations

router = APIRouter(prefix="/api/reports", tags=["reports"])
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "tpl.html"


def _build_markdown_report(project_name: str, agent_outputs: list[dict]) -> str:
    """Build the markdown report from agent outputs."""
    report_data = None
    chair_data = None
    ray_data = None

    for ao in agent_outputs:
        if ao["agent_name"] == "report_writer":
            report_data = ao.get("output", {})
        elif ao["agent_name"] == "committee_chair":
            chair_data = ao.get("output", {})
        elif ao["agent_name"] == "ray_dalio":
            ray_data = ao.get("output", {})

    sections = report_data.get("sections", {}) if report_data else {}
    merged_footnotes: list[dict] = []

    report_footnotes = normalize_footnotes(report_data.get("footnotes", []) if report_data else [])
    chair_footnotes = normalize_footnotes(chair_data.get("footnotes", []) if chair_data else [])
    ray_footnotes = normalize_footnotes(ray_data.get("footnotes", []) if ray_data else [])

    def cited_text(value, footnotes):
        if isinstance(value, str):
            return reindex_citations(value, footnotes, merged_footnotes)[0]
        if isinstance(value, list):
            remapped = []
            for item in value:
                if isinstance(item, str):
                    remapped.append(reindex_citations(item, footnotes, merged_footnotes)[0])
                else:
                    remapped.append(item)
            return remapped
        return value

    lines = []
    lines.append(f"# Investment Committee Report: {project_name}")
    lines.append("")
    lines.append(f"**Date:** {report_data.get('report_date', datetime.utcnow().strftime('%Y-%m-%d')) if report_data else datetime.utcnow().strftime('%Y-%m-%d')}")
    lines.append("")

    if chair_data:
        decision = chair_data.get("decision", "N/A")
        conviction = chair_data.get("conviction_level", "N/A")
        sizing = chair_data.get("position_sizing", "N/A")
        entry = chair_data.get("entry_strategy", "N/A")
        review = chair_data.get("review_date", "N/A")
        chair_reasoning = cited_text(chair_data.get("reasoning", "N/A"), chair_footnotes)

        lines.append("---")
        lines.append("")
        lines.append(f"## DECISION: {decision}")
        lines.append("")
        lines.append(f"- **Conviction:** {conviction}")
        lines.append(f"- **Position Size:** {sizing}")
        lines.append(f"- **Entry Strategy:** {entry}")
        lines.append(f"- **Review Date:** {review}")
        lines.append("")
        lines.append(f"**Chair's Reasoning:** {chair_reasoning}")
        lines.append("")
        lines.append("---")
        lines.append("")

    score_data = sections.get("21_score_breakdown", {})
    overall = sections.get("22_overall_score", "N/A")

    lines.append("## Score Breakdown")
    lines.append("")
    lines.append(f"**Overall Score: {overall}**")
    lines.append("")
    if isinstance(score_data, dict):
        lines.append("| Domain | Score |")
        lines.append("|--------|-------|")
        for domain, score in score_data.items():
            lines.append(f"| {domain.replace('_', ' ').title()} | {score} |")
        lines.append("")

    section_titles = {
        "1_executive_summary": "Executive Summary",
        "2_project_overview": "Project Overview",
        "3_tokenomics": "Tokenomics",
        "4_governance": "Governance",
        "5_on_chain_metrics": "On-Chain Metrics",
        "6_technical_architecture": "Technical Architecture",
        "7_competitive_landscape": "Competitive Landscape",
        "8_community_sentiment": "Community & Sentiment",
        "9_team_assessment": "Team Assessment",
        "10_legal_regulatory": "Legal & Regulatory",
        "11_risk_assessment": "Risk Assessment",
        "12_maturation_analysis": "Maturation Analysis",
        "13_revenue_analysis": "Revenue Analysis",
        "14_portfolio_fit": "Portfolio Fit",
        "15_investment_thesis_alignment": "Thesis Alignment",
        "16_bull_case": "Bull Case",
        "17_bear_case": "Bear Case",
    }
    
    for key, title in section_titles.items():
        content = cited_text(sections.get(key, ""), report_footnotes)
        if content:
            lines.append(f"## {title}")
            lines.append("")
            lines.append(str(content))
            lines.append("")

    risks = cited_text(sections.get("18_key_risks", []), report_footnotes)
    opps = cited_text(sections.get("19_key_opportunities", []), report_footnotes)

    if risks:
        lines.append("## Key Risks")
        lines.append("")
        for i, r in enumerate(risks if isinstance(risks, list) else [risks], 1):
            r = str(r).lstrip("0123456789. ")
            lines.append(f"{i}. {r}")
        lines.append("")
    
    if opps:
        lines.append("## Key Opportunities")
        lines.append("")
        for i, o in enumerate(opps if isinstance(opps, list) else [opps], 1):
            o = str(o).lstrip("0123456789. ")
            lines.append(f"{i}. {o}")
        lines.append("")

    mandate = sections.get("20_mandate_compliance", "")
    if mandate:
        lines.append("## Mandate Compliance")
        lines.append("")
        lines.append(str(cited_text(mandate, report_footnotes)))
        lines.append("")

    if ray_data:
        ray_summary = cited_text(ray_data.get("summary", "N/A"), ray_footnotes)
        ray_verdict = cited_text(ray_data.get("rays_verdict", "N/A"), ray_footnotes)
        lines.append("---")
        lines.append("")
        lines.append("## Ray's Independent Review")
        lines.append("")
        lines.append(f"**Verdict:** {ray_verdict}")
        lines.append("")
        lines.append(f"**Summary:** {ray_summary}")
        lines.append("")
        
        for field, label in [
            ("inversion_analysis", "Inversion (How do we lose money?)"),
            ("circle_of_competence", "Circle of Competence"),
            ("margin_of_safety", "Margin of Safety"),
            ("incentive_analysis", "Incentive Analysis"),
            ("stupidity_check", "Stupidity Check"),
        ]:
            val = cited_text(ray_data.get(field, ""), ray_footnotes)
            if val:
                lines.append(f"**{label}:** {val}")
                lines.append("")

    signposts = cited_text(sections.get("24_signposts_to_monitor", []), report_footnotes)
    if not signposts and chair_data:
        signposts = cited_text(chair_data.get("signposts", []), chair_footnotes)

    if signposts:
        lines.append("---")
        lines.append("")
        lines.append("## Signposts to Monitor")
        lines.append("")
        for s in signposts:
            lines.append(f"- {s}")
        lines.append("")

    if chair_data and chair_data.get("conflicts_resolved"):
        conflicts = cited_text(chair_data.get("conflicts_resolved", []), chair_footnotes)
        lines.append("## Conflicts Resolved")
        lines.append("")
        for c in conflicts:
            lines.append(f"- {c}")
        lines.append("")

    if merged_footnotes:
        lines.append("---")
        lines.append("")
        lines.append("## Footnotes")
        lines.append("")
        for footnote in merged_footnotes:
            support = f" — {footnote['supports']}" if footnote.get("supports") else ""
            lines.append(f"[{footnote['id']}] [{footnote['label']}]({footnote['url']}){support}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Committee Orchestrator*")

    return "\n".join(lines)


@router.get("/{evaluation_id}/markdown", response_class=PlainTextResponse)
async def get_markdown_report(evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a formatted markdown report for an evaluation."""

    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    if evaluation.status != "completed":
        raise HTTPException(status_code=400, detail=f"Evaluation status is {evaluation.status}")

    proj_result = await db.execute(
        select(Project).where(Project.id == evaluation.project_id)
    )
    project = proj_result.scalar_one_or_none()
    project_name = project.name if project else "Unknown"

    outputs_result = await db.execute(
        select(AgentOutput).where(AgentOutput.evaluation_id == evaluation.id)
    )
    outputs = outputs_result.scalars().all()
    
    agent_outputs = [
        {
            "agent_name": o.agent_name,
            "output": o.output,
            "score": float(o.score) if o.score else None,
        }
        for o in outputs
    ]

    markdown = _build_markdown_report(project_name, agent_outputs)
    return markdown


@router.get("")
async def list_reports(db: AsyncSession = Depends(get_db)):
    """List all completed evaluations."""
    result = await db.execute(
        select(Evaluation, Project)
        .join(Project, Evaluation.project_id == Project.id)
        .where(Evaluation.status == "completed")
        .order_by(Evaluation.completed_at.desc())
    )
    rows = result.all()
    
    return {
        "reports": [
            {
                "evaluation_id": str(e.id),
                "project_name": p.name,
                "ticker": p.ticker,
                "completed_at": e.completed_at,
            }
            for e, p in rows
        ]
    }

@router.get("/{evaluation_id}/html")
async def get_html(evaluation_id: UUID, db: AsyncSession = Depends(get_db)):
    from starlette.responses import HTMLResponse
    md = await get_markdown_report(evaluation_id, db)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return HTMLResponse(template.replace("MARKER", json.dumps(md)))
