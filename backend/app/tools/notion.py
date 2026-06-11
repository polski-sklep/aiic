"""Notion integration for storing and retrieving notes, transcripts, and learnings.

Notion is the human-readable layer. Agents read from it via search,
and write evaluation outputs back to it. pgvector handles semantic search
by syncing embeddings from Notion content.

Required Notion setup:
1. Create an integration at https://www.notion.so/my-integrations
2. Create 3 databases and share them with the integration:

   TRANSCRIPTS database properties:
     - Title (title)
     - Source (select: zoom, manual, teams, etc.)
     - Call Date (date)
     - Status (select: raw, summarized, indexed)
     - Tags (multi_select)

   LEARNINGS database properties:
     - Title (title)
     - Category (select: risk_pattern, success_signal, red_flag, market_insight, etc.)
     - Project (rich_text)
     - Source (select: evaluation, manual, ic_call)
     - Date (date)

   PROJECTS database properties:
     - Name (title)
     - Ticker (rich_text)
     - Category (select: L1, L2, DeFi, Infrastructure, etc.)
     - Last Score (number)
     - Recommendation (select: INVEST, WATCH, PASS, VETO)
     - Last Evaluated (date)
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypedDict, cast

from notion_client import AsyncClient

from app.config import get_settings

logger = logging.getLogger(__name__)


class NotionSearchResult(TypedDict, total=False):
    page_id: str
    title: str
    properties: dict[str, object]
    type: str
    last_edited: str | None
    url: str


class NotionPageContent(TypedDict):
    page_id: str
    title: str
    properties: dict[str, object]
    content: str
    last_edited: str | None
    url: str


def get_notion_client() -> AsyncClient:
    settings = get_settings()
    if not settings.notion_api_key:
        raise RuntimeError("NOTION_API_KEY not configured")
    return AsyncClient(auth=settings.notion_api_key)


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _join_plain_text(items: object) -> str:
    return "".join(
        str(_as_mapping(item).get("plain_text", ""))
        for item in _as_sequence(items)
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_notion(query: str, database_id: str | None = None, limit: int = 10) -> list[NotionSearchResult]:
    """Search Notion pages by text query.

    If database_id is provided, searches within that database.
    Otherwise searches across all shared pages.
    """
    client = get_notion_client()

    if database_id:
        response = await client.databases.query(
            database_id=database_id,
            filter={
                "or": [
                    {"property": "title", "title": {"contains": query}},
                ]
            },
            page_size=limit,
        )
        return _parse_db_results(cast(list[Mapping[str, object]], response.get("results", [])))

    response = await client.search(
        query=query,
        page_size=limit,
        sort={"direction": "descending", "timestamp": "last_edited_time"},
    )
    return _parse_search_results(cast(list[Mapping[str, object]], response.get("results", [])))


async def get_page_content(page_id: str) -> NotionPageContent:
    """Retrieve full content of a Notion page (blocks)."""
    client = get_notion_client()

    page = cast(Mapping[str, object], await client.pages.retrieve(page_id=page_id))

    blocks: list[Mapping[str, object]] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, object] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = await client.blocks.children.list(**kwargs)
        blocks.extend(cast(list[Mapping[str, object]], response.get("results", [])))
        if not response.get("has_more"):
            break
        cursor = cast(str | None, response.get("next_cursor"))

    text_content = _blocks_to_text(blocks)

    return {
        "page_id": page_id,
        "title": _extract_title(page),
        "properties": _extract_properties(page),
        "content": text_content,
        "last_edited": cast(str | None, page.get("last_edited_time")),
        "url": str(page.get("url", "")),
    }


# ---------------------------------------------------------------------------
# Create / Write
# ---------------------------------------------------------------------------

async def create_transcript(
    title: str,
    content: str,
    source: str = "manual",
    call_date: datetime | None = None,
    tags: list[str] | None = None,
) -> str:
    """Create a transcript page in the Transcripts database."""
    settings = get_settings()
    if not settings.notion_transcripts_db:
        raise RuntimeError("NOTION_TRANSCRIPTS_DB not configured")

    client = get_notion_client()

    properties: dict[str, object] = {
        "title": {"title": [{"text": {"content": title}}]},
        "Source": {"select": {"name": source}},
        "Status": {"select": {"name": "raw"}},
    }
    if call_date:
        properties["Call Date"] = {"date": {"start": call_date.isoformat()}}
    if tags:
        properties["Tags"] = {"multi_select": [{"name": tag} for tag in tags]}

    children = _text_to_blocks(content)

    page = cast(
        Mapping[str, object],
        await client.pages.create(
            parent={"database_id": settings.notion_transcripts_db},
            properties=properties,
            children=children,
        ),
    )

    logger.info("Created transcript page: %s", page["id"])
    return str(page["id"])


async def create_learning(
    title: str,
    content: str,
    category: str = "market_insight",
    project_name: str = "",
    source: str = "evaluation",
) -> str:
    """Create a learning entry in the Learnings database."""
    settings = get_settings()
    if not settings.notion_learnings_db:
        raise RuntimeError("NOTION_LEARNINGS_DB not configured")

    client = get_notion_client()

    properties: dict[str, object] = {
        "title": {"title": [{"text": {"content": title}}]},
        "Category": {"select": {"name": category}},
        "Source": {"select": {"name": source}},
        "Date": {"date": {"start": datetime.now(timezone.utc).date().isoformat()}},
    }
    if project_name:
        properties["Project"] = {"rich_text": [{"text": {"content": project_name}}]}

    children = _text_to_blocks(content)

    page = cast(
        Mapping[str, object],
        await client.pages.create(
            parent={"database_id": settings.notion_learnings_db},
            properties=properties,
            children=children,
        ),
    )

    logger.info("Created learning page: %s", page["id"])
    return str(page["id"])


async def update_project_evaluation(
    project_name: str,
    ticker: str = "",
    category: str = "",
    score: float | None = None,
    recommendation: str = "",
    report_summary: str = "",
) -> str:
    """Create or update a project entry in the Projects database after evaluation."""
    settings = get_settings()
    if not settings.notion_projects_db:
        raise RuntimeError("NOTION_PROJECTS_DB not configured")

    client = get_notion_client()

    existing = cast(
        Mapping[str, object],
        await client.databases.query(
            database_id=settings.notion_projects_db,
            filter={"property": "Name", "title": {"equals": project_name}},
            page_size=1,
        ),
    )

    properties: dict[str, object] = {
        "Name": {"title": [{"text": {"content": project_name}}]},
        "Last Evaluated": {"date": {"start": datetime.now(timezone.utc).date().isoformat()}},
    }
    if ticker:
        properties["Ticker"] = {"rich_text": [{"text": {"content": ticker}}]}
    if category:
        properties["Category"] = {"select": {"name": category}}
    if score is not None:
        properties["Last Score"] = {"number": round(score, 1)}
    if recommendation:
        properties["Recommendation"] = {"select": {"name": recommendation}}

    existing_results = cast(list[Mapping[str, object]], existing.get("results", []))
    if existing_results:
        page_id = str(existing_results[0]["id"])
        await client.pages.update(page_id=page_id, properties=properties)

        if report_summary:
            children = _text_to_blocks(
                f"\n---\n**Evaluation {datetime.now(timezone.utc).strftime('%Y-%m-%d')}**\n\n{report_summary}"
            )
            await client.blocks.children.append(block_id=page_id, children=children)

        logger.info("Updated project page: %s", page_id)
        return page_id

    children = _text_to_blocks(report_summary) if report_summary else []
    page = cast(
        Mapping[str, object],
        await client.pages.create(
            parent={"database_id": settings.notion_projects_db},
            properties=properties,
            children=children,
        ),
    )
    logger.info("Created project page: %s", page["id"])
    return str(page["id"])


# ---------------------------------------------------------------------------
# Sync: Notion -> pgvector
# ---------------------------------------------------------------------------

async def sync_database_to_pgvector(database_id: str, source_type: str) -> int:
    """Sync all pages from a Notion database into pgvector knowledge_chunks.

    Returns number of chunks created.
    """
    from app.knowledge import generate_embedding, chunk_text
    from app.database import async_session
    from app.models import KnowledgeChunk

    client = get_notion_client()
    chunks_created = 0

    cursor: str | None = None
    while True:
        kwargs: dict[str, object] = {"database_id": database_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = cast(Mapping[str, object], await client.databases.query(**kwargs))

        for page in cast(list[Mapping[str, object]], response.get("results", [])):
            page_id = str(page["id"])
            title = _extract_title(page)
            page_data = await get_page_content(page_id)
            full_text = f"{title}\n\n{page_data['content']}"

            if not full_text.strip():
                continue

            chunks = chunk_text(full_text)
            async with async_session() as session:
                for chunk in chunks:
                    embedding = await generate_embedding(chunk)
                    obj = KnowledgeChunk(
                        source_type=source_type,
                        source_id=None,
                        content=chunk,
                        embedding=embedding,
                        metadata_={"notion_page_id": page_id, "title": title},
                    )
                    session.add(obj)
                    chunks_created += 1
                await session.commit()

        if not response.get("has_more"):
            break
        cursor = cast(str | None, response.get("next_cursor"))

    logger.info("Synced %s chunks from Notion database %s", chunks_created, database_id)
    return chunks_created


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blocks_to_text(blocks: Sequence[Mapping[str, object]]) -> str:
    """Convert Notion blocks to plain text."""
    parts: list[str] = []
    for block in blocks:
        block_type = str(block.get("type", ""))
        block_data = _as_mapping(block.get(block_type, {}))

        if "rich_text" in block_data:
            text = _join_plain_text(block_data.get("rich_text", []))
            if block_type.startswith("heading") and block_type[-1].isdigit():
                text = f"{'#' * int(block_type[-1])} {text}"
            elif block_type == "bulleted_list_item":
                text = f"• {text}"
            elif block_type == "numbered_list_item":
                text = f"- {text}"
            parts.append(text)
        elif block_type == "divider":
            parts.append("---")
        elif block_type == "code":
            code_text = _join_plain_text(block_data.get("rich_text", []))
            lang = str(block_data.get("language", ""))
            parts.append(f"```{lang}\n{code_text}\n```")

    return "\n".join(parts)


def _text_to_blocks(text: str, max_block_size: int = 1900) -> list[dict[str, object]]:
    """Convert plain text to Notion paragraph blocks."""
    blocks: list[dict[str, object]] = []
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_block_size:
            if current_chunk:
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": current_chunk}}]
                        },
                    }
                )
            current_chunk = line
        else:
            current_chunk = f"{current_chunk}\n{line}" if current_chunk else line

    if current_chunk:
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": current_chunk}}]
                },
            }
        )

    return blocks


def _extract_title(page: Mapping[str, object]) -> str:
    """Extract title from a Notion page object."""
    props = _as_mapping(page.get("properties", {}))
    for prop in props.values():
        prop_map = _as_mapping(prop)
        if prop_map.get("type") == "title":
            title_parts = prop_map.get("title", [])
            title = _join_plain_text(title_parts)
            if title:
                return title
    return "Untitled"


def _extract_properties(page: Mapping[str, object]) -> dict[str, object]:
    """Extract simplified properties from a Notion page."""
    result: dict[str, object] = {}
    for key, prop in _as_mapping(page.get("properties", {})).items():
        prop_map = _as_mapping(prop)
        ptype = str(prop_map.get("type", ""))
        if ptype == "title":
            result[key] = _join_plain_text(prop_map.get("title", []))
        elif ptype == "rich_text":
            result[key] = _join_plain_text(prop_map.get("rich_text", []))
        elif ptype == "select":
            sel = _as_mapping(prop_map.get("select", {}))
            result[key] = sel.get("name")
        elif ptype == "multi_select":
            result[key] = [
                _as_mapping(item).get("name")
                for item in _as_sequence(prop_map.get("multi_select", []))
            ]
        elif ptype == "number":
            result[key] = prop_map.get("number")
        elif ptype == "date":
            d = _as_mapping(prop_map.get("date", {}))
            result[key] = d.get("start")
        elif ptype == "checkbox":
            result[key] = prop_map.get("checkbox")
    return result


def _parse_db_results(results: Sequence[Mapping[str, object]]) -> list[NotionSearchResult]:
    """Parse database query results into simplified format."""
    return [
        {
            "page_id": str(result["id"]),
            "title": _extract_title(result),
            "properties": _extract_properties(result),
            "last_edited": cast(str | None, result.get("last_edited_time")),
            "url": str(result.get("url", "")),
        }
        for result in results
    ]


def _parse_search_results(results: Sequence[Mapping[str, object]]) -> list[NotionSearchResult]:
    """Parse search results into simplified format."""
    parsed: list[NotionSearchResult] = []
    for result in results:
        object_type = str(result.get("object", ""))
        title = _extract_title(result) if object_type == "page" else _join_plain_text(result.get("title", []))
        entry: NotionSearchResult = {
            "page_id": str(result["id"]),
            "title": title or "Untitled",
            "last_edited": cast(str | None, result.get("last_edited_time")),
            "url": str(result.get("url", "")),
        }
        if object_type:
            entry["type"] = object_type
        parsed.append(entry)
    return parsed
