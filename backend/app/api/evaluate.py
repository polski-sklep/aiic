from __future__ import annotations
import logging
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Project, Evaluation, AgentOutput
from app.agents.orchestrator import Orchestrator

router = APIRouter(prefix="/api", tags=["evaluate"])
logger = logging.getLogger(__name__)


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

        evaluation.status = "completed"
        evaluation.completed_at = datetime.now(timezone.utc)

        return EvaluateResponse(
            evaluation_id=str(evaluation.id),
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
