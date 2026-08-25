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
            children=batch_blocks(children)[0],
        ),
    )
    page_id = str(page["id"])
    # pages.create is bound by the same limits as an append; the rest follows.
    await append_blocks(page_id, children[len(batch_blocks(children)[0]):], client=client)

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
            children=batch_blocks(children)[0],
        ),
    )
    page_id = str(page["id"])
    # pages.create is bound by the same limits as an append; the rest follows.
    await append_blocks(page_id, children[len(batch_blocks(children)[0]):], client=client)

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
    into paragraphs. Whichever is supplied, the body is written in batches
    Notion will accept so a long run does not 400 the request.

    On a page that already exists the run is written to the *top*, under the
    history header, so the newest evaluation is the one a reader sees first
    (see `prepend_blocks`). Nothing already on the page is moved or deleted.
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

        written = await prepend_blocks(page_id, children, client=client)
        logger.info("Updated project page %s (%s blocks written to the top)", page_id, written)
        return page_id

    # A new page is built with the history header as its first child, so every
    # later run has a stable anchor to be inserted after. pages.create is bound
    # by the same two limits as an append.
    body: list[dict[str, object]] = [history_header_block(), *children]
    head = batch_blocks(body)[0]
    tail = body[len(head):]
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
        # The first run's own tail still belongs below its head, so this one
        # append is the only place the newest run is extended downwards.
        await append_blocks(page_id, tail, client=client)
    logger.info("Created project page %s (%s blocks)", page_id, len(body))
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
NOTION_CHILDREN_LIMIT = 100    # top-level blocks per children.append / pages.create
NOTION_TOTAL_BLOCKS_LIMIT = 1000  # blocks per request INCLUDING nested children
NOTION_URL_LIMIT = 2000        # characters in a link url

# Hosts that a Notion reader's browser cannot resolve. The VPS runs with
# BACKEND_URL=http://localhost:8100 (verified 25 Aug 2026), which is correct for
# the container and useless as a hyperlink in a page Jacob opens on his laptop.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"})

# The Tailscale address the Telegram bot already serves report links from
# (COMMITTEE_REPORT_BASE in its own env), used only when BACKEND_URL is a
# loopback address and so cannot be linked. Setting BACKEND_URL to a reachable
# address on the VPS is the supported way to change this; it is read through
# get_settings() rather than os.environ because CONTRACTS 3.5 forbids modules
# reading configuration from the environment directly.
_FALLBACK_REPORT_BASE = "http://100.95.239.105:8100"


def resolve_report_base() -> str:
    """Return a base URL for report links that is reachable from a browser.

    `settings.backend_url` is the container's own address. On the VPS it is
    `http://localhost:8100` (verified 25 Aug 2026) — correct for the service,
    dead as a hyperlink in a page Jacob opens on his laptop. When it points at
    a loopback host the Tailscale address is used instead, which is the address
    the Telegram bot already hands out for the same two endpoints.
    """
    from urllib.parse import urlsplit

    backend = (get_settings().backend_url or "").strip().rstrip("/")
    if backend and urlsplit(backend).hostname not in _LOOPBACK_HOSTS:
        return backend

    logger.info(
        "backend_url=%r is a loopback address and cannot be linked from Notion; "
        "using %s. Set BACKEND_URL to a reachable address to change this.",
        backend,
        _FALLBACK_REPORT_BASE,
    )
    return _FALLBACK_REPORT_BASE


def split_text(text: str, limit: int = NOTION_TEXT_LIMIT) -> list[str]:
    """Split text into pieces of at most `limit` characters, on word boundaries.

    Exactly lossless: `"".join(split_text(t)) == t`. The whitespace a break
    falls on is carried into the following piece rather than stripped — Notion
    merges adjacent rich-text runs that share annotations, so stripping it
    would silently weld the two words either side of every 2,000-character
    boundary together in the stored page.
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
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
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
            continue  # only ever the empty string, which Notion rejects
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


def _cap_with_marker(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    """Cap a rich-text array where the block type cannot be split into siblings.

    Used only for callouts and headings, which must stay single blocks. The cut
    is marked in the text rather than made silently — see the module header.
    """
    if len(objects) <= NOTION_RICH_TEXT_LIMIT:
        return objects
    kept = objects[:NOTION_RICH_TEXT_LIMIT - 1]
    kept.extend(rich_text(" … (truncated to fit Notion's 100-run limit)", italic=True))
    return kept[:NOTION_RICH_TEXT_LIMIT]


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
    payload: dict[str, object] = {"rich_text": _cap_with_marker(inline_rich_text(text))}
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
            "rich_text": _cap_with_marker(objects),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def truncation_notice(what: str) -> dict[str, object]:
    """A visible marker. Content is never cut without one of these."""
    return callout_block(
        rich_text(f"Truncated to fit Notion's block limits: {what}", italic=True),
        emoji="✂️",
        color="orange_background",
    )


def toggle_block(
    summary: list[dict[str, object]],
    children: list[dict[str, object]],
) -> dict[str, object]:
    """A collapsible section.

    A toggle's children go out inside the parent block, so they are bounded by
    the same 100-element limit and cannot be sent as a follow-up append. If
    there are more, the last slot carries a visible notice saying so rather
    than the content just ending.
    """
    kept = children
    if len(children) > NOTION_CHILDREN_LIMIT:
        kept = children[:NOTION_CHILDREN_LIMIT - 1]
        kept.append(truncation_notice(f"{len(children) - len(kept)} further blocks in this section"))
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": _cap_with_marker(summary),
            "children": kept,
        },
    }


def block_weight(block: Mapping[str, object]) -> int:
    """Total blocks a payload entry costs, counting nested children."""
    block_type = str(block.get("type", ""))
    children = _as_sequence(_as_mapping(block.get(block_type, {})).get("children", []))
    return 1 + sum(block_weight(_as_mapping(child)) for child in children)


def batch_blocks(
    blocks: list[dict[str, object]],
    size: int = NOTION_CHILDREN_LIMIT,
    total_limit: int = NOTION_TOTAL_BLOCKS_LIMIT,
) -> list[list[dict[str, object]]]:
    """Split a block list into batches Notion will accept.

    Two separate limits apply and only the first is obvious: at most 100
    entries in the `children` array, AND at most 1,000 blocks in the request
    once nested children are counted. A page of collapsed per-agent sections
    hits the second one first — 100 toggles of 15 children each is 1,500 blocks
    in a request whose array length is a perfectly legal 100. Verified against
    the live API on 25 Aug 2026: "Number of blocks in the request exceeds limit
    of 1000."
    """
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    weight = 0

    for block in blocks:
        cost = block_weight(block)
        if current and (len(current) >= size or weight + cost > total_limit):
            batches.append(current)
            current = []
            weight = 0
        current.append(block)
        weight += cost

    if current:
        batches.append(current)
    return batches or [[]]


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
# Newest-first ordering
#
# A project is re-evaluated repeatedly and the page grows without limit, so the
# run a reader wants is the newest one and it must be at the top. Appending put
# it at the bottom, which is how two runs came to read as one mashed page.
#
# Notion offers no prepend. `PATCH /v1/blocks/{block_id}/children` takes an
# optional `after` naming an existing *child* block; there is no `before`, no
# move/reorder endpoint, and no way to address absolute position zero. Verified
# against the live API on 25 Aug 2026:
#
#   after=<child block id>  -> inserted directly after that child      (works)
#   after=<the page id>     -> 400 "Block ID (…) to append children after
#                              is not parented by (…)"
#
# So the page carries one block we own — a header callout — as its first child,
# and every run is inserted immediately after it. That header is the anchor:
# stable, self-describing, and created exactly once per page.
#
# Two further live findings shape the code below:
#
#   * The append response's `results` is NOT just the blocks created. With
#     `after` set it returns the created blocks followed by every sibling that
#     already came after them, so `results[-1]` is the last block on the page,
#     not the last block written. The created blocks come first and in order,
#     so the last one written is `results[len(batch) - 1]`. A multi-batch run
#     that chained on `results[-1]` would look correct for a single batch and
#     scramble on the second.
#   * Nested children (a toggle's contents) are not returned in `results` at
#     all, so the positional rule counts top-level entries only.
#
# Batches are therefore chained forward — each after the last block the
# previous one wrote — rather than all inserted after the anchor. Inserting
# every batch after the anchor would also produce the right order (each new
# insert pushes the previous one down, confirmed live) but reverses the failure
# mode: a part-written run would keep its tail and lose its head, and the head
# is the decision callout, the report links and the evaluation id. Chaining
# forward keeps the prefix, matching `append_blocks`.
# ---------------------------------------------------------------------------

# Marker text identifying the anchor. Matched on read, so it is a constant
# rather than a literal at each site; changing it orphans existing headers and
# a fresh one will be created beneath the old.
HISTORY_HEADER_TEXT = "Evaluation history — newest first"


def history_header_block() -> dict[str, object]:
    """The page's first block: the anchor every run is inserted after."""
    objects = rich_text(HISTORY_HEADER_TEXT, bold=True)
    objects.extend(
        rich_text(
            "  ·  the most recent evaluation is directly below this block.",
            italic=True,
            color="gray",
        )
    )
    return callout_block(objects, emoji="🕒", color="gray_background")


def _rich_text_content(items: object) -> str:
    """Text of a rich-text array, as read back *or* as built here.

    A block returned by the API carries `plain_text`; one built by this module
    has only `text.content`. `is_history_header` is asked about both — the
    freshly built block and the one listed back off the page — so it reads
    either.
    """
    parts: list[str] = []
    for item in _as_sequence(items):
        entry = _as_mapping(item)
        text = entry.get("plain_text")
        if text is None:
            text = _as_mapping(entry.get("text", {})).get("content", "")
        parts.append(str(text))
    return "".join(parts)


def is_history_header(block: Mapping[str, object]) -> bool:
    """True if `block` is the anchor written by `history_header_block`."""
    if str(block.get("type", "")) != "callout":
        return False
    text = _rich_text_content(_as_mapping(block.get("callout", {})).get("rich_text", []))
    return text.startswith(HISTORY_HEADER_TEXT)


def _normalise_id(value: object) -> str:
    """Notion ids are the same id dashed or undashed; compare them normalised."""
    return str(value).replace("-", "").lower()


def _last_created_block_id(
    response: Mapping[str, object],
    batch_size: int,
    parent_id: str,
) -> str:
    """Id of the last top-level block an append actually created.

    Not `results[-1]`: with `after` set the response continues past the new
    blocks into the siblings that already followed them (verified live). The
    created blocks lead the array, so the one wanted is at `batch_size - 1`.
    """
    parent = _normalise_id(parent_id)
    direct: list[Mapping[str, object]] = []
    for entry in _as_sequence(response.get("results", [])):
        block = _as_mapping(entry)
        if not block.get("id"):
            continue
        owner = _as_mapping(block.get("parent", {}))
        owner_id = owner.get("page_id") or owner.get("block_id")
        if owner_id is not None and _normalise_id(owner_id) != parent:
            continue  # a nested child, which cannot be used as an anchor
        direct.append(block)

    if len(direct) >= batch_size >= 1:
        return str(direct[batch_size - 1]["id"])
    if direct:
        return str(direct[-1]["id"])
    raise RuntimeError(
        "Notion append returned no usable block id; cannot position the next batch"
    )


# How far down the page to look for an existing header. It is written at index
# 0 on a page this module created and at index 1 on one it adopted, so a small
# window suffices — and searching a window rather than only the first child is
# what stops a second header being created on every run of an adopted page.
# The slack above 1 lets the header still be found if someone types a note in
# above it, in which case new runs go below that note, which is what they meant.
_HEADER_SEARCH_WINDOW = 10


async def history_anchor_id(page_id: str, client: AsyncClient | None = None) -> str:
    """Return the anchor to insert new runs after, creating it if absent.

    On a page this module created, the header is already the first child. On a
    page written before newest-first ordering it is not, and there is no way to
    put it at absolute position zero — so it goes immediately after whatever is
    first (in practice the leading divider of the oldest run). Everything below
    the header is then correctly ordered newest-first from that point on; only
    that one legacy block stays stranded above it.

    The header is looked for across the first few blocks, not just the first.
    Checking only index 0 created a fresh header on every run of an adopted
    page, because on such a page the header lives at index 1 and never moves up
    — found on the live API, not in the unit tests, which ran a single write.
    """
    client = client or get_notion_client()
    response = cast(
        Mapping[str, object],
        await client.blocks.children.list(block_id=page_id, page_size=_HEADER_SEARCH_WINDOW),
    )
    results = [_as_mapping(item) for item in _as_sequence(response.get("results", []))]

    for block in results:
        if is_history_header(block):
            return str(block["id"])

    if not results:
        created = cast(
            Mapping[str, object],
            await client.blocks.children.append(
                block_id=page_id, children=[history_header_block()]
            ),
        )
        return _last_created_block_id(created, 1, page_id)

    created = cast(
        Mapping[str, object],
        await client.blocks.children.append(
            block_id=page_id,
            children=[history_header_block()],
            after=str(results[0]["id"]),
        ),
    )
    logger.info("Added the newest-first header to existing Notion page %s", page_id)
    return _last_created_block_id(created, 1, page_id)


async def prepend_blocks(
    page_id: str,
    blocks: list[dict[str, object]],
    client: AsyncClient | None = None,
) -> int:
    """Insert blocks at the top of a page, below the history header.

    The counterpart of `append_blocks` and bound by the same two limits, so the
    same `batch_blocks` budgeting applies. Batches are chained forward, so a
    failure part-way leaves the head of the run on the page rather than its
    tail — see the section header for why that direction was chosen.
    """
    if not blocks:
        return 0
    client = client or get_notion_client()

    anchor = await history_anchor_id(page_id, client=client)

    written = 0
    for batch in batch_blocks(blocks):
        if not batch:
            continue
        response = cast(
            Mapping[str, object],
            await client.blocks.children.append(
                block_id=page_id, children=batch, after=anchor
            ),
        )
        anchor = _last_created_block_id(response, len(batch), page_id)
        written += len(batch)
    return written


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
