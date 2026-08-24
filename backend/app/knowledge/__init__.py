from __future__ import annotations
import logging
from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict, cast

from app.utils.types import JSONObject
from app.config import get_settings

logger = logging.getLogger(__name__)


class SemanticSearchRow(Protocol):
    id: object
    content: str
    source_type: str
    metadata: JSONObject
    similarity: object


class SemanticSearchResult(TypedDict):
    id: str
    content: str
    source_type: str
    metadata: JSONObject
    similarity: float


async def generate_embedding(text: str) -> list[float]:
    """Generate embedding vector using OpenAI's embedding model.

    Using OpenAI for embeddings regardless of primary LLM provider
    because text-embedding-3-small is cheap ($0.02/1M tokens) and
    produces 1536-dim vectors compatible with pgvector.
    """
    settings = get_settings()

    if settings.openai_api_key:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text[:8000],  # Truncate to model limit
        )
        return response.data[0].embedding

    # Fallback: use Anthropic's voyager if available in future
    # For now, raise if no OpenAI key
    raise RuntimeError("OpenAI API key required for embeddings (text-embedding-3-small)")


SEARCHABLE_TABLES = frozenset({"knowledge_chunks", "learnings", "transcripts"})

# Empirically derived against the live 62-chunk corpus on 24 Aug 2026.
# text-embedding-3-small yields much lower absolute cosine similarities than the
# previous 0.7 default assumed: across 14 realistic agent queries x 5 results the
# highest similarity observed anywhere was 0.6317, so threshold=0.7 returned ZERO
# rows for EVERY query. 0.30 separates signal from noise cleanly -- queries for
# projects absent from the corpus ("Lido" 0.1915, "EigenLayer" 0.2131) fall below
# it and correctly return nothing, while every in-corpus query keeps its true
# hits (weakest true positive 0.3006).
# Full measurement: docs/reviews/retrieval-evaluation.md
DEFAULT_SIMILARITY_THRESHOLD = 0.30


async def semantic_search(
    query: str,
    table: Literal["knowledge_chunks", "learnings", "transcripts"] = "knowledge_chunks",
    limit: int = 5,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[SemanticSearchResult]:
    """Search for semantically similar content in pgvector.

    Args:
        query: Search query text
        table: Table to search (knowledge_chunks, learnings, transcripts)
        limit: Max results
        threshold: Minimum cosine similarity (0-1). See
            DEFAULT_SIMILARITY_THRESHOLD for why the default is 0.30, not 0.7.
    """
    from sqlalchemy import text as sql_text
    from app.database import async_session

    # `table` is interpolated into the SQL below, so it must never be
    # caller-controlled text. The Literal annotation is not enforced at runtime.
    if table not in SEARCHABLE_TABLES:
        raise ValueError(f"Unknown table for semantic search: {table!r}")

    embedding = await generate_embedding(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    async with async_session() as session:
        result = await session.execute(
            sql_text(f"""
                SELECT id, content, source_type, metadata,
                       1 - (embedding <=> CAST(:embedding AS vector)) as similarity
                FROM {table}
                WHERE embedding IS NOT NULL
                  AND 1 - (embedding <=> CAST(:embedding AS vector)) > :threshold
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """),
            {
                "embedding": embedding_str,
                "threshold": threshold,
                "limit": limit,
            },
        )
        rows = cast(Sequence[SemanticSearchRow], result.fetchall())

    return [
        {
            "id": str(row.id),
            "content": row.content,
            "source_type": row.source_type,
            "metadata": row.metadata,
            "similarity": round(float(row.similarity), 4),
        }
        for row in rows
    ]


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for embedding.

    Args:
        text: Input text
        chunk_size: Target characters per chunk
        overlap: Character overlap between chunks
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        # Try to break at sentence boundary
        if end < len(text):
            # Look for sentence end near chunk boundary
            for sep in [". ", ".\n", "\n\n", "\n", " "]:
                idx = text.rfind(sep, start + chunk_size // 2, end + 100)
                if idx > 0:
                    end = idx + len(sep)
                    break

        chunks.append(text[start:end].strip())
        start = end - overlap

    return [c for c in chunks if c]  # Filter empty
