from __future__ import annotations
import httpx
from typing import TypedDict, cast

from app.llm import ToolDefinition
from app.tools.contracts import ToolRegistrar
from app.tools.http_errors import (
    BAD_REQUEST,
    NOT_CONFIGURED,
    ToolFailure,
    http_failure,
    tool_failure,
    transport_failure,
)
from app.utils.types import ToolArguments
from app.config import get_settings


BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

SERVICE = "Brave Search"


class WebSearchItem(TypedDict):
    title: str | None
    url: str | None
    description: str


class WebSearchResult(TypedDict):
    query: str
    result_count: int
    results: list[WebSearchItem]


ToolError = ToolFailure


async def web_search(args: ToolArguments) -> WebSearchResult | ToolError:
    """Search the web using Brave Search API.

    QA-028: the tool had no status handling, so a Brave 429 raised out of it and
    reached the agent as the registry's generic wrapper, while zero results came
    back as a clean empty success. Only the second means "there is nothing to
    find". The success envelope is unchanged; it is the failures that now say
    what they are.
    """
    query = str(args.get("query", "") or "").strip()
    count = _coerce_count(args.get("count"))

    if not query:
        return tool_failure(BAD_REQUEST, "No query was supplied.")

    settings = get_settings()
    if not settings.brave_search_api_key:
        return tool_failure(NOT_CONFIGURED, "BRAVE_SEARCH_API_KEY not configured")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                BRAVE_URL,
                params={"q": query, "count": count},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": settings.brave_search_api_key,
                },
            )
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if resp.status_code != 200:
        return http_failure(SERVICE, resp.status_code)

    try:
        data = cast(dict[str, object], resp.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

    web_section = cast(dict[str, object], data.get("web", {}))
    result_items = cast(list[dict[str, object]], web_section.get("results", []))
    results: list[WebSearchItem] = []
    for item in result_items[:count]:
        results.append(
            {
                "title": cast(str | None, item.get("title")),
                "url": cast(str | None, item.get("url")),
                "description": str(item.get("description", ""))[:300],
            }
        )

    return {
        "query": query,
        "result_count": len(results),
        "results": results,
    }


def _coerce_count(raw: object, default: int = 5, maximum: int = 10) -> int:
    """``int(args.get("count", 5) or 5)`` raised on any non-numeric value."""
    if isinstance(raw, bool) or raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, maximum)


def register(registry: ToolRegistrar) -> None:
    registry.register(
        ToolDefinition(
            name="web_search",
            description="Search the web for recent information about a crypto project, team, news, or any topic. Returns titles, URLs, and descriptions.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results (max 10, default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        web_search,
    )
