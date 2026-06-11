from __future__ import annotations
import httpx
from typing import TYPE_CHECKING, TypedDict, cast

from app.llm import ToolDefinition
from app.tools.registry import ToolArguments
from app.config import get_settings

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class WebSearchItem(TypedDict):
    title: str | None
    url: str | None
    description: str


class WebSearchResult(TypedDict):
    query: str
    result_count: int
    results: list[WebSearchItem]


class ToolError(TypedDict, total=False):
    error: str


async def web_search(args: ToolArguments) -> WebSearchResult | ToolError:
    """Search the web using Brave Search API."""
    query = str(args.get("query", "")).strip()
    count = min(int(args.get("count", 5) or 5), 10)

    settings = get_settings()
    if not settings.brave_search_api_key:
        return {"error": "BRAVE_SEARCH_API_KEY not configured"}

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
        resp.raise_for_status()
        data = cast(dict[str, object], resp.json())

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


def register(registry: ToolRegistry) -> None:
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
