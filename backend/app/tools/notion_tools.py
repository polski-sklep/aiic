"""Notion tools registered in the tool registry for agent use."""
from __future__ import annotations
from typing import TYPE_CHECKING, TypedDict, cast

from app.llm import ToolDefinition
from app.tools.registry import ToolArguments
from app.tools.notion import NotionPageContent, NotionSearchResult, get_page_content, search_notion
from app.config import get_settings
from app.utils import KnowledgeDatabase

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry


class SearchNotesResult(TypedDict):
    query: str
    database: KnowledgeDatabase
    result_count: int
    results: list[NotionSearchResult]


class ReadNoteResult(TypedDict):
    page_id: str
    title: str
    properties: dict[str, object]
    content: str
    last_edited: str | None
    url: str


class ToolError(TypedDict, total=False):
    error: str


async def search_notes(args: ToolArguments) -> SearchNotesResult | ToolError:
    """Search Notion for notes, transcripts, and past evaluations."""
    query = str(args.get("query", "")).strip()
    database = cast(KnowledgeDatabase, str(args.get("database", "all")))

    settings = get_settings()
    if not settings.notion_api_key:
        return {"error": "Notion not configured"}

    db_id = None
    if database == "transcripts" and settings.notion_transcripts_db:
        db_id = settings.notion_transcripts_db
    elif database == "learnings" and settings.notion_learnings_db:
        db_id = settings.notion_learnings_db
    elif database == "projects" and settings.notion_projects_db:
        db_id = settings.notion_projects_db

    results = await search_notion(query, database_id=db_id, limit=5)
    return {
        "query": query,
        "database": database,
        "result_count": len(results),
        "results": results,
    }


async def read_note(args: ToolArguments) -> ReadNoteResult | ToolError:
    """Read full content of a Notion page by ID."""
    page_id = str(args.get("page_id", "")).strip()
    if not page_id:
        return {"error": "page_id is required"}

    settings = get_settings()
    if not settings.notion_api_key:
        return {"error": "Notion not configured"}

    page_data: NotionPageContent = await get_page_content(page_id)
    # Truncate content for agent context window
    content = page_data["content"]
    if len(content) > 4000:
        content = content[:4000] + "\n\n[TRUNCATED - content exceeds 4000 chars]"

    return {
        "page_id": page_data["page_id"],
        "title": page_data["title"],
        "properties": page_data["properties"],
        "content": content,
        "last_edited": page_data["last_edited"],
        "url": page_data["url"],
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="search_notes",
            description=(
                "Search Notion knowledge base for past IC call transcripts, learnings, "
                "project evaluations, and research notes. Use to find prior discussions, "
                "historical context, and team knowledge about a project or topic."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (project name, topic, keyword)",
                    },
                    "database": {
                        "type": "string",
                        "enum": ["all", "transcripts", "learnings", "projects"],
                        "description": "Which database to search. 'all' searches everywhere.",
                        "default": "all",
                    },
                },
                "required": ["query"],
            },
        ),
        search_notes,
    )

    registry.register(
        ToolDefinition(
            name="read_note",
            description=(
                "Read the full content of a specific Notion page. Use after search_notes "
                "returns a relevant result and you need the full text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Notion page ID from search_notes results",
                    },
                },
                "required": ["page_id"],
            },
        ),
        read_note,
    )
