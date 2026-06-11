from __future__ import annotations
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.utils import KnowledgeDatabase
from app.tools.notion import (
    search_notion,
    get_page_content,
    create_transcript,
    create_learning,
    sync_database_to_pgvector,
)
from app.knowledge import semantic_search

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class TranscriptCreate(BaseModel):
    title: str
    content: str
    source: str = "manual"
    call_date: str | None = None  # ISO date
    tags: list[str] = Field(default_factory=list)


class LearningCreate(BaseModel):
    title: str
    content: str
    category: str = "market_insight"
    project_name: str = ""
    source: str = "manual"


class SearchRequest(BaseModel):
    query: str
    limit: int = 5
    threshold: float = 0.7


# ---------------------------------------------------------------------------
# Semantic search (pgvector)
# ---------------------------------------------------------------------------

@router.post("/search")
async def knowledge_search(req: SearchRequest):
    """Semantic search across all indexed knowledge (pgvector)."""
    results = await semantic_search(
        query=req.query,
        table="knowledge_chunks",
        limit=req.limit,
        threshold=req.threshold,
    )
    return {"query": req.query, "results": results}


# ---------------------------------------------------------------------------
# Notion: search
# ---------------------------------------------------------------------------

@router.get("/notion/search")
async def notion_search(query: str, database: KnowledgeDatabase = "all", limit: int = 10):
    """Search Notion directly (text-based, not semantic)."""
    settings = get_settings()
    if not settings.notion_api_key:
        raise HTTPException(status_code=503, detail="Notion not configured")

    db_map = {
        "transcripts": settings.notion_transcripts_db,
        "learnings": settings.notion_learnings_db,
        "projects": settings.notion_projects_db,
    }
    db_id = db_map.get(database)

    results = await search_notion(query, database_id=db_id, limit=limit)
    return {"query": query, "database": database, "results": results}


@router.get("/notion/page/{page_id}")
async def notion_page(page_id: str):
    """Get full content of a Notion page."""
    settings = get_settings()
    if not settings.notion_api_key:
        raise HTTPException(status_code=503, detail="Notion not configured")

    return await get_page_content(page_id)


# ---------------------------------------------------------------------------
# Notion: create
# ---------------------------------------------------------------------------

@router.post("/transcripts")
async def add_transcript(req: TranscriptCreate):
    """Add a transcript to Notion."""
    settings = get_settings()
    if not settings.notion_api_key or not settings.notion_transcripts_db:
        raise HTTPException(status_code=503, detail="Notion transcripts DB not configured")

    call_date = None
    if req.call_date:
        call_date = datetime.fromisoformat(req.call_date)

    page_id = await create_transcript(
        title=req.title,
        content=req.content,
        source=req.source,
        call_date=call_date,
        tags=req.tags,
    )
    return {"page_id": page_id, "status": "created"}


@router.post("/learnings")
async def add_learning(req: LearningCreate):
    """Add a learning to Notion."""
    settings = get_settings()
    if not settings.notion_api_key or not settings.notion_learnings_db:
        raise HTTPException(status_code=503, detail="Notion learnings DB not configured")

    page_id = await create_learning(
        title=req.title,
        content=req.content,
        category=req.category,
        project_name=req.project_name,
        source=req.source,
    )
    return {"page_id": page_id, "status": "created"}


# ---------------------------------------------------------------------------
# Sync: Notion → pgvector
# ---------------------------------------------------------------------------

@router.post("/sync")
async def sync_notion_to_pgvector(database: KnowledgeDatabase = "all"):
    """Sync Notion databases to pgvector for semantic search.

    Run this after adding new content to Notion, or on a schedule.
    """
    settings = get_settings()
    if not settings.notion_api_key:
        raise HTTPException(status_code=503, detail="Notion not configured")

    total = 0
    synced = {}

    db_map = {
        "transcripts": (settings.notion_transcripts_db, "transcript"),
        "learnings": (settings.notion_learnings_db, "learning"),
        "projects": (settings.notion_projects_db, "project_evaluation"),
    }

    for name, (db_id, source_type) in db_map.items():
        if database not in ("all", name):
            continue
        if not db_id:
            synced[name] = "skipped (not configured)"
            continue

        count = await sync_database_to_pgvector(db_id, source_type)
        synced[name] = count
        total += count

    return {"total_chunks": total, "databases": synced}
