from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Project, Evaluation

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    ticker: str | None = None
    chain: str | None = None
    category: str | None = None
    website: str | None = None
    coingecko_id: str | None = None


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all evaluated projects."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return {
        "projects": [
            {
                "id": str(p.id),
                "name": p.name,
                "ticker": p.ticker,
                "chain": p.chain,
                "category": p.category,
                "website": p.website,
                "created_at": p.created_at,
            }
            for p in projects
        ]
    }


@router.post("")
async def create_project(req: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new project."""
    project = Project(**req.model_dump())
    db.add(project)
    await db.flush()
    return {"id": str(project.id), "name": project.name}


@router.get("/{project_id}")
async def get_project(project_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get project details with evaluation history.

    `project_id` is typed `UUID` so FastAPI rejects a malformed id with a 422
    before the handler runs. It used to be `str`, handed straight to
    `uuid.UUID(...)`, which raised ValueError and surfaced as a 500 — the same
    pattern `api/reports.py` and `api/evaluate.py` already get right.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    evals_result = await db.execute(
        select(Evaluation)
        .where(Evaluation.project_id == project.id)
        .order_by(Evaluation.created_at.desc())
    )
    evaluations = evals_result.scalars().all()

    return {
        "id": str(project.id),
        "name": project.name,
        "ticker": project.ticker,
        "chain": project.chain,
        "category": project.category,
        "website": project.website,
        "coingecko_id": project.coingecko_id,
        "created_at": project.created_at,
        "evaluations": [
            {
                "id": str(e.id),
                "status": e.status,
                "started_at": e.started_at,
                "completed_at": e.completed_at,
            }
            for e in evaluations
        ],
    }
