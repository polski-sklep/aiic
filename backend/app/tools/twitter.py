"""Twitter/X search tool using X API v2.

Requires X_BEARER_TOKEN in .env.
Minimal params for Free/Basic tier compatibility.
"""
from __future__ import annotations
import httpx
from typing import TypedDict, cast

from app.llm import ToolDefinition
from app.tools.contracts import ToolRegistrar
from app.tools.http_errors import (
    BAD_REQUEST,
    NOT_CONFIGURED,
    RATE_LIMITED,
    ToolFailure,
    http_failure,
    tool_failure,
    transport_failure,
)
from app.utils.types import ToolArguments
from app.config import get_settings


X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

SERVICE = "X API"


class Tweet(TypedDict):
    text: str
    id: str
    url: str


class TwitterSearchResult(TypedDict):
    query: str
    tweet_count: int
    tweets: list[Tweet]


ToolError = ToolFailure


def _coerce_max_results(raw: object, default: int = 10, maximum: int = 100) -> int:
    if isinstance(raw, bool) or raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 10:  # X API v2 rejects max_results below 10
        return default
    return min(value, maximum)


async def search_twitter(args: ToolArguments) -> TwitterSearchResult | ToolError:
    """Search recent tweets about a topic.

    QA-028: 401, 429 and 400 were handled and everything else was not, so a 403
    — a suspended app or the wrong access tier, which is what this project
    actually hits — escaped as an httpx exception while its neighbours returned
    clean dicts. The tool was inconsistent with itself depending on which
    failure occurred.
    """
    query = str(args.get("query", "") or "").strip()
    max_results = _coerce_max_results(args.get("max_results"))

    if not query:
        return tool_failure(BAD_REQUEST, "No query was supplied.")

    settings = get_settings()
    token = getattr(settings, "x_bearer_token", "")
    if not token:
        return tool_failure(
            NOT_CONFIGURED, "X_BEARER_TOKEN not configured. Add it to .env and restart."
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                X_SEARCH_URL,
                params={
                    "query": query,
                    "max_results": max_results,
                },
                headers={"Authorization": "Bearer " + token},
            )
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if resp.status_code == 401:
        return tool_failure(
            NOT_CONFIGURED, f"{SERVICE} authentication failed. Check X_BEARER_TOKEN."
        )
    if resp.status_code == 429:
        return tool_failure(RATE_LIMITED, f"{SERVICE} rate limit exceeded. Try again later.")
    if resp.status_code == 400:
        return tool_failure(
            BAD_REQUEST,
            f"{SERVICE} rejected the query. Try simpler search terms.",
            details=resp.text,
        )
    if resp.status_code != 200:
        return http_failure(SERVICE, resp.status_code)

    try:
        data = cast(dict[str, object], resp.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

    tweets: list[Tweet] = []
    for tweet in cast(list[dict[str, object]], data.get("data", [])):
        tweet_id = str(tweet.get("id", ""))
        tweets.append(
            {
                "text": str(tweet.get("text", ""))[:500],
                "id": tweet_id,
                "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else "",
            }
        )

    return {
        "query": query,
        "tweet_count": len(tweets),
        "tweets": tweets,
    }


def register(registry: ToolRegistrar) -> None:
    registry.register(
        ToolDefinition(
            name="search_twitter",
            description=(
                "Search recent tweets about a crypto project, protocol, or topic. "
                "Returns tweet text sorted by recency. "
                "Use for sentiment analysis, community health, breaking news, and alpha signals."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g., 'Aave v4', 'Chainlink CCIP')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of tweets (default 10, max 100)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        search_twitter,
    )
