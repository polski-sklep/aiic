"""Notion tools registered in the tool registry for agent use."""
from __future__ import annotations

import re
from typing import TypedDict

from app.config import get_settings
from app.llm import ToolDefinition
from app.tools.notion import NotionPageContent, NotionSearchResult, get_page_content, search_notion
from app.tools.contracts import ToolRegistrar
from app.tools.http_errors import BAD_REQUEST, NOT_CONFIGURED, tool_failure
from app.utils.types import ToolArguments
from app.utils import KnowledgeDatabase


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


#: Requestable database name -> the Settings attribute holding its id.
_DATABASE_SETTINGS = {
    "transcripts": "notion_transcripts_db",
    "learnings": "notion_learnings_db",
    "projects": "notion_projects_db",
}


async def search_notes(args: ToolArguments) -> SearchNotesResult | ToolError:
    """Search Notion for notes, transcripts, and past evaluations.

    QA-034: when the requested database had no id configured, ``db_id`` stayed
    None and the search ran across *everything* — while the result still echoed
    ``{"database": "learnings"}``. The agent, and every later reader of
    ``agent_outputs``, was told the search was scoped when it was not, so
    results from other databases were attributed to the one asked for. Any
    unrecognised database value behaved the same way.

    An unscoped search is still available, by asking for it: ``database: "all"``.
    """
    query = str(args.get("query", "") or "").strip()
    database = str(args.get("database", "all") or "all").strip().lower()

    settings = get_settings()
    if not settings.notion_api_key:
        return {"error": "Notion not configured"}

    if database != "all" and database not in _DATABASE_SETTINGS:
        return tool_failure(
            BAD_REQUEST,
            f"Unknown Notion database {database!r}. Use one of: "
            f"all, {', '.join(sorted(_DATABASE_SETTINGS))}.",
        )

    db_id = None
    if database != "all":
        db_id = getattr(settings, _DATABASE_SETTINGS[database], "") or None
        if db_id is None:
            return tool_failure(
                NOT_CONFIGURED,
                f"The Notion '{database}' database is not configured, so no search was run. "
                f"Searching unscoped and labelling the results '{database}' would have "
                f"attributed other databases' notes to it.",
            )

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

    cleaned_page_id = page_id.replace("-", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", cleaned_page_id):
        return {
            "page_id": page_id,
            "title": "No matching prior note",
            "properties": {},
            "content": f"No prior note found for '{page_id}' because it is not a valid Notion page reference.",
            "last_edited": None,
            "url": "",
        }

    settings = get_settings()
    if not settings.notion_api_key:
        return {"error": "Notion not configured"}

    page_data: NotionPageContent = await get_page_content(page_id)
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


def register(registry: ToolRegistrar) -> None:
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
