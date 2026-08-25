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
import re
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

    blocks = await _list_children(client, page_id)
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
            children=children[:NOTION_CHILDREN_LIMIT],
        ),
    )
    page_id = str(page["id"])
    # pages.create caps `children` at 100 blocks; long content lands as appends.
    await append_blocks(page_id, children[NOTION_CHILDREN_LIMIT:], client=client)

    logger.info("Created transcript page: %s (%s blocks)", page_id, len(children))
    return page_id


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
            children=children[:NOTION_CHILDREN_LIMIT],
        ),
    )
    page_id = str(page["id"])
    # pages.create caps `children` at 100 blocks; long content lands as appends.
    await append_blocks(page_id, children[NOTION_CHILDREN_LIMIT:], client=client)

    logger.info("Created learning page: %s (%s blocks)", page_id, len(children))
    return page_id


async def update_project_evaluation(
    project_name: str,
    ticker: str = "",
    category: str = "",
    score: float | None = None,
    recommendation: str = "",
    report_summary: str = "",
    report_blocks: list[dict[str, object]] | None = None,
) -> str:
    """Create or update a project entry in the Projects database after evaluation.

    `report_blocks` is the preferred input: a list of already-built Notion
    blocks (see `orchestrator._notion_write`). `report_summary` is the older
    text path and is parsed as markdown into real blocks rather than dumped
    into paragraphs. Whichever is supplied, the body is appended in
    100-block batches so a long run does not 400 the request.
    """
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

    if report_blocks is not None:
        children: list[dict[str, object]] = list(report_blocks)
    elif report_summary:
        children = [divider_block()]
        children.append(
            heading_block(f"Evaluation {datetime.now(timezone.utc).strftime('%Y-%m-%d')}", 1)
        )
        children.extend(_text_to_blocks(report_summary))
    else:
        children = []

    existing_results = cast(list[Mapping[str, object]], existing.get("results", []))
    if existing_results:
        page_id = str(existing_results[0]["id"])
        await client.pages.update(page_id=page_id, properties=properties)

        appended = await append_blocks(page_id, children, client=client)
        logger.info("Updated project page %s (%s blocks appended)", page_id, appended)
        return page_id

    # pages.create also caps `children` at 100; the remainder is appended.
    head, tail = children[:NOTION_CHILDREN_LIMIT], children[NOTION_CHILDREN_LIMIT:]
    page = cast(
        Mapping[str, object],
        await client.pages.create(
            parent={"database_id": settings.notion_projects_db},
            properties=properties,
            children=head,
        ),
    )
    page_id = str(page["id"])
    if tail:
        await append_blocks(page_id, tail, client=client)
    logger.info("Created project page %s (%s blocks)", page_id, len(children))
    return page_id


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

async def _list_children(
    client: AsyncClient,
    block_id: str,
    depth: int = 2,
) -> list[Mapping[str, object]]:
    """List a block's children, descending into nested blocks.

    Toggles hold the per-agent findings, and their children are a separate
    fetch. Without this recursion `get_page_content` — and therefore the
    Notion->pgvector sync — would read a project page as a list of empty toggle
    headers, silently losing the archive it is there to preserve.
    """
    blocks: list[Mapping[str, object]] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, object] = {"block_id": block_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = await client.blocks.children.list(**kwargs)
        for block in cast(list[Mapping[str, object]], response.get("results", [])):
            entry = dict(block)
            if block.get("has_children") and depth > 0:
                entry["_children"] = await _list_children(client, str(block["id"]), depth - 1)
            blocks.append(entry)
        if not response.get("has_more"):
            break
        cursor = cast(str | None, response.get("next_cursor"))
    return blocks


def _blocks_to_text(blocks: Sequence[Mapping[str, object]], indent: str = "") -> str:
    """Convert Notion blocks to plain text, including nested children."""
    parts: list[str] = []
    for block in blocks:
        block_type = str(block.get("type", ""))
        block_data = _as_mapping(block.get(block_type, {}))

        if block_type == "code":
            code_text = _join_plain_text(block_data.get("rich_text", []))
            lang = str(block_data.get("language", ""))
            parts.append(f"{indent}```{lang}\n{code_text}\n```")
        elif "rich_text" in block_data:
            text = _join_plain_text(block_data.get("rich_text", []))
            if block_type.startswith("heading") and block_type[-1].isdigit():
                text = f"{'#' * int(block_type[-1])} {text}"
            elif block_type == "bulleted_list_item":
                text = f"• {text}"
            elif block_type == "numbered_list_item":
                text = f"- {text}"
            elif block_type == "quote":
                text = f"> {text}"
            elif block_type == "callout":
                icon = _as_mapping(block_data.get("icon", {})).get("emoji", "")
                text = f"{icon} {text}".strip()
            elif block_type == "toggle":
                text = f"▸ {text}"
            parts.append(f"{indent}{text}")
        elif block_type == "divider":
            parts.append(f"{indent}---")

        children = block.get("_children") or _as_mapping(block.get(block_type, {})).get("children")
        if children:
            nested = _blocks_to_text(cast(Sequence[Mapping[str, object]], children), indent + "  ")
            if nested:
                parts.append(nested)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Notion block construction
#
# Notion's API does not interpret markdown inside a rich-text `content` field.
# Bold is `annotations.bold`, headings are `heading_N` blocks, bullets are
# `bulleted_list_item` blocks. Writing "**name**" into a paragraph therefore
# renders the asterisks literally, which is exactly what every project page
# written before this module looked like.
#
# For five of the six projects in the calibration corpus the Notion page is the
# only surviving record of the committee's reasoning (docs/CONTRACTS.md 2.5),
# so this is an archival surface. Two rules follow from that:
#   * nothing is dropped silently — over-long text is split, and on the one
#     path where a hard cap can still bite, a visible marker is written into
#     the page saying so;
#   * every API limit below is enforced here rather than discovered as a 400
#     from `children.append` halfway through a 15-agent run.
# ---------------------------------------------------------------------------

# Hard Notion API limits. https://developers.notion.com/reference/request-limits
NOTION_TEXT_LIMIT = 2000       # characters per rich-text object
NOTION_RICH_TEXT_LIMIT = 100   # rich-text objects per array
NOTION_CHILDREN_LIMIT = 100    # blocks per children.append / pages.create call
NOTION_URL_LIMIT = 2000        # characters in a link url

# Hosts that a Notion reader's browser cannot resolve. The VPS runs with
# BACKEND_URL=http://localhost:8100 (verified 25 Aug 2026), which is correct for
# the container and useless as a hyperlink in a page Jacob opens on his laptop.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"})

# The Tailscale address the Telegram bot already serves report links from, used
# only when nothing better is configured. Set COMMITTEE_REPORT_BASE (the same
# variable telegram_bot.py reads) to override.
_FALLBACK_REPORT_BASE = "http://100.95.239.105:8100"


def resolve_report_base() -> str:
    """Return a base URL for report links that is reachable from a browser.

    Order: COMMITTEE_REPORT_BASE, then settings.backend_url if it is not a
    loopback address, then the Tailscale address the bot uses.
    """
    import os
    from urllib.parse import urlsplit

    configured = os.environ.get("COMMITTEE_REPORT_BASE", "").strip()
    if configured:
        return configured.rstrip("/")

    backend = (get_settings().backend_url or "").strip().rstrip("/")
    if backend and urlsplit(backend).hostname not in _LOOPBACK_HOSTS:
        return backend

    logger.info(
        "backend_url=%r is not reachable from a browser; linking reports via %s. "
        "Set COMMITTEE_REPORT_BASE to change this.",
        backend,
        _FALLBACK_REPORT_BASE,
    )
    return _FALLBACK_REPORT_BASE


def split_text(text: str, limit: int = NOTION_TEXT_LIMIT) -> list[str]:
    """Split text into pieces of at most `limit` characters, on word boundaries.

    Never drops characters: concatenating the result (with single spaces where a
    break fell on whitespace) reproduces the input.
    """
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut <= limit // 2:  # no usable break point — hard cut
            cut = limit
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def rich_text(
    content: str,
    *,
    bold: bool = False,
    italic: bool = False,
    code: bool = False,
    underline: bool = False,
    strikethrough: bool = False,
    color: str = "",
    link: str = "",
) -> list[dict[str, object]]:
    """Build rich-text objects for one run of text, split to the 2,000-char cap."""
    annotations: dict[str, object] = {}
    if bold:
        annotations["bold"] = True
    if italic:
        annotations["italic"] = True
    if code:
        annotations["code"] = True
    if underline:
        annotations["underline"] = True
    if strikethrough:
        annotations["strikethrough"] = True
    if color:
        annotations["color"] = color

    href = link[:NOTION_URL_LIMIT] if link else ""

    objects: list[dict[str, object]] = []
    for piece in split_text(content):
        if not piece:
            continue
        text_payload: dict[str, object] = {"content": piece}
        if href:
            text_payload["link"] = {"url": href}
        obj: dict[str, object] = {"type": "text", "text": text_payload}
        if annotations:
            obj["annotations"] = dict(annotations)
        objects.append(obj)
    return objects


# Inline markdown: [text](url), **bold**, `code`, _italic_ / *italic*.
_INLINE_PATTERN = re.compile(
    r"\[(?P<ltext>[^\]\n]*)\]\((?P<lurl>[^\s)]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>[^`\n]+)`"
    r"|(?<![A-Za-z0-9_])_(?P<uital>[^_\n]+)_(?![A-Za-z0-9_])"
    r"|(?<!\*)\*(?P<ital>[^*\n]+)\*(?!\*)",
    re.DOTALL,
)


def inline_rich_text(text: str) -> list[dict[str, object]]:
    """Convert inline markdown into annotated rich text.

    This is the fix for the literal `**` asterisks: `**tokenomics_analyst**`
    becomes a rich-text object with `annotations.bold = true` instead of four
    stray characters in a paragraph.
    """
    objects: list[dict[str, object]] = []
    cursor = 0
    for match in _INLINE_PATTERN.finditer(text):
        if match.start() > cursor:
            objects.extend(rich_text(text[cursor:match.start()]))
        if match.group("ltext") is not None:
            url = match.group("lurl")
            objects.extend(rich_text(match.group("ltext") or url, link=url))
        elif match.group("bold") is not None:
            objects.extend(rich_text(match.group("bold"), bold=True))
        elif match.group("code") is not None:
            objects.extend(rich_text(match.group("code"), code=True))
        elif match.group("uital") is not None:
            objects.extend(rich_text(match.group("uital"), italic=True))
        else:
            objects.extend(rich_text(match.group("ital"), italic=True))
        cursor = match.end()
    if cursor < len(text):
        objects.extend(rich_text(text[cursor:]))
    return objects or rich_text(text)


def _cap_rich_text(objects: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Chunk a rich-text array to Notion's 100-object limit, losing nothing."""
    if len(objects) <= NOTION_RICH_TEXT_LIMIT:
        return [objects]
    return [
        objects[i:i + NOTION_RICH_TEXT_LIMIT]
        for i in range(0, len(objects), NOTION_RICH_TEXT_LIMIT)
    ]


def _text_block(
    block_type: str,
    objects: list[dict[str, object]],
    extra: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """One or more blocks of `block_type`, splitting past the rich-text cap."""
    blocks: list[dict[str, object]] = []
    for chunk in _cap_rich_text(objects):
        payload: dict[str, object] = {"rich_text": chunk}
        if extra:
            payload.update(extra)
        blocks.append({"object": "block", "type": block_type, block_type: payload})
    return blocks


def heading_block(text: str, level: int = 2, *, toggleable: bool = False) -> dict[str, object]:
    level = min(max(level, 1), 3)
    block_type = f"heading_{level}"
    payload: dict[str, object] = {"rich_text": inline_rich_text(text)[:NOTION_RICH_TEXT_LIMIT]}
    if toggleable:
        payload["is_toggleable"] = True
    return {"object": "block", "type": block_type, block_type: payload}


def paragraph_blocks(text: str) -> list[dict[str, object]]:
    return _text_block("paragraph", inline_rich_text(text))


def rich_paragraph_block(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    return _text_block("paragraph", objects)


def bullet_blocks(text: str) -> list[dict[str, object]]:
    return _text_block("bulleted_list_item", inline_rich_text(text))


def rich_bullet_block(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    return _text_block("bulleted_list_item", objects)


def numbered_blocks(text: str) -> list[dict[str, object]]:
    return _text_block("numbered_list_item", inline_rich_text(text))


def quote_blocks(text: str) -> list[dict[str, object]]:
    return _text_block("quote", inline_rich_text(text))


def code_block(text: str, language: str = "plain text") -> list[dict[str, object]]:
    return _text_block("code", rich_text(text), {"language": language})


def divider_block() -> dict[str, object]:
    return {"object": "block", "type": "divider", "divider": {}}


def callout_block(
    objects: list[dict[str, object]],
    *,
    emoji: str = "💡",
    color: str = "gray_background",
) -> dict[str, object]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": objects[:NOTION_RICH_TEXT_LIMIT],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def toggle_block(
    summary: list[dict[str, object]],
    children: list[dict[str, object]],
) -> dict[str, object]:
    """A collapsible section. Children are capped to the per-array limit."""
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": summary[:NOTION_RICH_TEXT_LIMIT],
            "children": children[:NOTION_CHILDREN_LIMIT],
        },
    }


def truncation_notice(what: str) -> dict[str, object]:
    """A visible marker. Content is never cut without one of these."""
    return callout_block(
        rich_text(f"Truncated to fit Notion's block limits: {what}", italic=True),
        emoji="✂️",
        color="orange_background",
    )


def batch_blocks(
    blocks: list[dict[str, object]],
    size: int = NOTION_CHILDREN_LIMIT,
) -> list[list[dict[str, object]]]:
    """Split a block list into children.append-sized batches."""
    return [blocks[i:i + size] for i in range(0, len(blocks), size)] or [[]]


async def append_blocks(
    page_id: str,
    blocks: list[dict[str, object]],
    client: AsyncClient | None = None,
) -> int:
    """Append blocks to a page, batching to the 100-per-request limit.

    Returns the number of blocks appended. Batches are sent in order so a
    failure part-way leaves a prefix of the run on the page rather than nothing.
    """
    if not blocks:
        return 0
    client = client or get_notion_client()
    appended = 0
    for batch in batch_blocks(blocks):
        if not batch:
            continue
        await client.blocks.children.append(block_id=page_id, children=batch)
        appended += len(batch)
    return appended


# ---------------------------------------------------------------------------
# Markdown -> blocks
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_DIVIDER_RE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")


def _text_to_blocks(text: str, max_block_size: int = NOTION_TEXT_LIMIT) -> list[dict[str, object]]:
    """Convert markdown-ish text into real Notion blocks.

    Headings, bullets, numbered items, quotes, dividers and fenced code become
    the corresponding block types; `**bold**`, `_italic_`, `` `code` `` and
    `[text](url)` become annotations rather than literal characters.
    """
    blocks: list[dict[str, object]] = []
    paragraph: list[str] = []
    code_lines: list[str] | None = None
    code_lang = "plain text"

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            joined = "\n".join(paragraph).strip()
            if joined:
                blocks.extend(_text_block("paragraph", inline_rich_text(joined)))
            paragraph = []

    for line in text.split("\n"):
        if code_lines is not None:
            if line.strip().startswith("```"):
                blocks.extend(code_block("\n".join(code_lines), code_lang))
                code_lines = None
                code_lang = "plain text"
            else:
                code_lines.append(line)
            continue

        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            code_lines = []
            code_lang = stripped[3:].strip() or "plain text"
            continue

        if not stripped:
            flush_paragraph()
            continue

        if _DIVIDER_RE.match(stripped):
            flush_paragraph()
            blocks.append(divider_block())
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            blocks.append(heading_block(heading.group(2), min(len(heading.group(1)), 3)))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_paragraph()
            blocks.extend(bullet_blocks(bullet.group(1)))
            continue

        numbered = _NUMBERED_RE.match(line)
        if numbered:
            flush_paragraph()
            blocks.extend(numbered_blocks(numbered.group(1)))
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            flush_paragraph()
            blocks.extend(quote_blocks(quote.group(1)))
            continue

        paragraph.append(line)

    if code_lines is not None:  # unterminated fence
        blocks.extend(code_block("\n".join(code_lines), code_lang))
    flush_paragraph()

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
