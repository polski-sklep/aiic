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


async def semantic_search(
    query: str,
    table: Literal["knowledge_chunks", "learnings", "transcripts"] = "knowledge_chunks",
    limit: int = 5,
    threshold: float = 0.7,
) -> list[SemanticSearchResult]:
    """Search for semantically similar content in pgvector.

    Args:
        query: Search query text
        table: Table to search (knowledge_chunks, learnings, transcripts)
        limit: Max results
        threshold: Minimum cosine similarity (0-1)
    """
    from sqlalchemy import text as sql_text
    from app.database import async_session

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
