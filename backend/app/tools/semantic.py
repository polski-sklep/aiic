"""Semantic (pgvector) retrieval registered as an agent tool.

Registered *beside* `search_notes`, not in place of it. The two search
different corpora and fail in different directions, which is why both exist:

  search_notes    -> Notion, live, every shared page including the ~9 KB project
                     evaluation write-ups and IC transcripts. Matches on words.
  semantic_search -> pgvector `knowledge_chunks`, a point-in-time embedded
                     snapshot of the Learnings database ONLY. Matches on meaning.

Measured head-to-head on the live corpus (docs/reviews/retrieval-evaluation.md):
keyword wins on exact project names and correctly returns nothing for projects
it has never seen; semantic wins on paraphrased concept queries, where it
retrieves prior risk analysis that shares no literal vocabulary with the query.
The tool description below encodes that split so agents pick the right one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from app.knowledge import DEFAULT_SIMILARITY_THRESHOLD, semantic_search
from app.llm import ToolDefinition
from app.utils.types import ToolArguments

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

MAX_LIMIT = 10
DEFAULT_LIMIT = 5


class SemanticHit(TypedDict):
    title: str
    content: str
    similarity: float
    notion_page_id: str


class SemanticSearchToolResult(TypedDict):
    query: str
    corpus: str
    result_count: int
    results: list[SemanticHit]


class ToolError(TypedDict, total=False):
    error: str


async def semantic_search_notes(
    args: ToolArguments,
) -> SemanticSearchToolResult | ToolError:
    """Meaning-based search over the embedded knowledge base."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}

    try:
        limit = min(int(args.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    try:
        rows = await semantic_search(
            query=query,
            table="knowledge_chunks",
            limit=limit,
            threshold=DEFAULT_SIMILARITY_THRESHOLD,
        )
    except RuntimeError as exc:
        # generate_embedding raises this when OPENAI_API_KEY is absent. Surface
        # it distinctly: a missing key and a genuine zero-result miss look
        # identical to the caller otherwise.
        return {"error": f"Semantic search unavailable: {exc}"}

    results: list[SemanticHit] = []
    for row in rows:
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        results.append(
            {
                "title": str(metadata.get("title", "")),
                "content": row["content"],
                "similarity": row["similarity"],
                "notion_page_id": str(metadata.get("notion_page_id", "")),
            }
        )

    return {
        "query": query,
        "corpus": "embedded snapshot of the Notion Learnings database",
        "result_count": len(results),
        "results": results,
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="semantic_search_notes",
            description=(
                "Search past committee LEARNINGS by meaning rather than by keyword. "
                "Finds prior risk analysis that describes the same idea in different "
                "words — e.g. the query 'risk of team tokens being dumped on the "
                "market' retrieves a learning titled 'perpetual selling pressure from "
                "team-controlled supply'.\n\n"
                "WHEN TO USE THIS INSTEAD OF search_notes:\n"
                "- Use search_notes FIRST, always, when you have a project NAME "
                "(e.g. 'Ethena', 'Aave'). It searches all of Notion live and is the "
                "only tool that can reach the full project evaluation write-ups and "
                "IC call transcripts. This tool cannot see those at all.\n"
                "- Use semantic_search_notes when you are looking for a PATTERN, "
                "RISK TYPE or CONCEPT across projects rather than one named project — "
                "'governance capture by insiders', 'supply overhang from cliff "
                "vesting', 'protocol that never turns on revenue sharing'. Keyword "
                "search misses these when the prior note used different wording.\n"
                "- Both are cheap. On a concept question, running both and merging is "
                "reasonable.\n\n"
                "SCOPE LIMITS — do not over-trust this tool:\n"
                "- It searches ONLY an embedded snapshot of the Learnings database. "
                "Project evaluations and transcripts are NOT indexed.\n"
                "- The snapshot is not live; learnings added to Notion since the last "
                "sync are absent. An empty result means 'not in the snapshot', not "
                "'the committee never considered this'.\n"
                "- Each result is a short standalone risk statement, not a full "
                "document. Use read_note with the returned notion_page_id for the "
                "source page."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A concept, risk pattern or question phrased in natural "
                            "language. Full phrases work better than single keywords."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Number of results (max {MAX_LIMIT}, default {DEFAULT_LIMIT})",
                        "default": DEFAULT_LIMIT,
                    },
                },
                "required": ["query"],
            },
        ),
        semantic_search_notes,
    )
