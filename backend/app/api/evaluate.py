from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.database import get_db
from app.models import Project, Evaluation, AgentOutput, Report
from app.agents.orchestrator import Orchestrator

router = APIRouter(prefix="/api", tags=["evaluate"])
logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Coerce an arbitrary pipeline result into something JSONB will accept.

    Agent outputs are parsed from LLM responses and are normally plain JSON,
    but a stray datetime or Decimal must not be able to fail the insert and
    lose the whole evaluation.
    """
    return json.loads(json.dumps(value, default=str))


def _extract_summary(eval_result: dict) -> str:
    """Best available one-paragraph summary for the report row."""
    draft = eval_result.get("draft_report") or {}
    if isinstance(draft, dict):
        summary = draft.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary

    chair = (eval_result.get("agent_results") or {}).get("committee_chair") or {}
    chair_output = chair.get("output") or {} if isinstance(chair, dict) else {}
    if isinstance(chair_output, dict):
        for key in ("summary", "reasoning"):
            candidate = chair_output.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate

    reasoning = eval_result.get("chair_reasoning")
    return reasoning if isinstance(reasoning, str) else ""


async def _persist_report(
    db: AsyncSession,
    evaluation: Evaluation,
    eval_result: dict,
) -> Report:
    """Write a `reports` row for a finished evaluation.

    The table existed with an index and a foreign key but was written by
    nothing — `api/reports.py` rebuilt markdown from `agent_outputs` at request
    time. This is the write side.

    Versions are additive: a re-run against the same evaluation appends
    version N+1 rather than overwriting, so earlier reasoning survives.
    """
    max_version = await db.execute(
        select(func.max(Report.version)).where(Report.evaluation_id == evaluation.id)
    )
    next_version = int(max_version.scalar() or 0) + 1

    report = Report(
        evaluation_id=evaluation.id,
        version=next_version,
        content=_jsonable(eval_result),
        summary=_extract_summary(eval_result),
        recommendation=eval_result.get("recommendation", "INSUFFICIENT_DATA"),
        overall_score=eval_result.get("overall_score"),
        risk_score=eval_result.get("risk_score"),
        vetoed=bool(eval_result.get("vetoed", False)),
        veto_reason=eval_result.get("veto_reason"),
    )
    db.add(report)
    return report


class EvaluateRequest(BaseModel):
    project_name: str
    ticker: str | None = None
    chain: str | None = None
    category: str | None = None
    website: str | None = None
    coingecko_id: str | None = None
    additional_context: str = ""


class EvaluateResponse(BaseModel):
    evaluation_id: str
    project_id: str
    status: str
    project_name: str
    scores: dict[str, float | None]
    overall_score: float | None
    recommendation: str
    agent_results: dict


@router.post("/evaluate", response_model=EvaluateResponse)
async def trigger_evaluation(
    req: EvaluateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a new project evaluation."""
    # Find or create project
    result = await db.execute(
        select(Project).where(Project.name == req.project_name)
    )
    project = result.scalar_one_or_none()

    if not project:
        project = Project(
            name=req.project_name,
            ticker=req.ticker,
            chain=req.chain,
            category=req.category,
            website=req.website,
            coingecko_id=req.coingecko_id,
        )
        db.add(project)
        await db.flush()

    # Create evaluation record
    evaluation = Evaluation(
        project_id=project.id,
        status="running",
        triggered_by="api",
        started_at=datetime.now(timezone.utc),
    )
    db.add(evaluation)
    await db.flush()

    # Commit before running the pipeline. Two reasons, the first load-bearing:
    #
    # 1. `record_calibration` runs inside the orchestrator on its OWN session,
    #    i.e. a separate connection and transaction, and inserts a row whose
    #    `calibration_records.evaluation_id` foreign key points at this row.
    #    A row that is only flushed is invisible to that other transaction, so
    #    the FK check fails immediately with ForeignKeyViolationError. The
    #    orchestrator catches it, logs "Calibration capture failed
    #    (non-fatal)" and continues — so passing a real evaluation_id without
    #    committing first would silently stop the calibration ledger recording
    #    anything at all. Verified against Postgres 16: with the flush alone
    #    record_calibration returns None and writes no row; after the commit it
    #    returns an id and the row is linked.
    # 2. A run is many minutes long. Committing here means the evaluation is
    #    visible as `running` for its whole duration instead of appearing only
    #    once it has finished.
    await db.commit()
    evaluation_id = str(evaluation.id)

    # Run orchestrator
    orchestrator = Orchestrator()
    try:
        eval_result = await orchestrator.evaluate(
            project_name=req.project_name,
            project_info={
                "ticker": req.ticker,
                "chain": req.chain,
                "category": req.category,
                "website": req.website,
                "coingecko_id": req.coingecko_id,
            },
            knowledge_context=req.additional_context,
            evaluation_id=evaluation_id,
        )

        # Store agent outputs
        for agent_name, agent_data in eval_result.get("agent_results", {}).items():
            agent_output = AgentOutput(
                evaluation_id=evaluation.id,
                agent_name=agent_name,
                model_used=agent_data.get("model_used", ""),
                output=agent_data.get("output", {}),
                score=agent_data.get("score"),
                tokens_input=agent_data.get("tokens_input", 0),
                tokens_output=agent_data.get("tokens_output", 0),
                latency_ms=agent_data.get("latency_ms"),
                error=agent_data.get("error"),
            )
            db.add(agent_output)

        # Persist the report itself rather than rebuilding it from
        # `agent_outputs` on every request.
        await _persist_report(db, evaluation, eval_result)

        evaluation.status = "completed"
        evaluation.completed_at = datetime.now(timezone.utc)

        return EvaluateResponse(
            evaluation_id=evaluation_id,
            project_id=str(project.id),
            status="completed",
            project_name=req.project_name,
            scores=eval_result.get("scores", {}),
            overall_score=eval_result.get("overall_score"),
            recommendation=eval_result.get("recommendation", "INSUFFICIENT_DATA"),
            agent_results=eval_result.get("agent_results", {}),
        )

    except Exception as e:
        evaluation.status = "failed"
        evaluation.error = str(e)
        evaluation.completed_at = datetime.now(timezone.utc)
        logger.exception("Evaluation failed for project %s", req.project_name)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        raise HTTPException(status_code=500, detail="Evaluation failed") from e


@router.get("/evaluate/{evaluation_id}")
async def get_evaluation(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get evaluation status and results."""
    result = await db.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
    )
    evaluation = result.scalar_one_or_none()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    # Get agent outputs
    outputs_result = await db.execute(
        select(AgentOutput).where(AgentOutput.evaluation_id == evaluation.id)
    )
    outputs = outputs_result.scalars().all()

    return {
        "id": str(evaluation.id),
        "project_id": str(evaluation.project_id),
        "status": evaluation.status,
        "error": evaluation.error,
        "started_at": evaluation.started_at,
        "completed_at": evaluation.completed_at,
        "agent_outputs": [
            {
                "agent_name": o.agent_name,
                "model_used": o.model_used,
                "output": o.output,
                "score": float(o.score) if o.score else None,
                "tokens_input": o.tokens_input,
                "tokens_output": o.tokens_output,
                "latency_ms": o.latency_ms,
                "error": o.error,
            }
            for o in outputs
        ],
    }
