"""Twitter/X search tool using X API v2.

Requires X_BEARER_TOKEN in .env.
Minimal params for Free/Basic tier compatibility.
"""
from __future__ import annotations
import httpx
from typing import TypedDict, cast

from app.llm import ToolDefinition
from app.tools.contracts import ToolRegistrar
from app.utils.types import ToolArguments
from app.config import get_settings


X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


class Tweet(TypedDict):
    text: str
    id: str
    url: str


class TwitterSearchResult(TypedDict):
    query: str
    tweet_count: int
    tweets: list[Tweet]


class ToolError(TypedDict, total=False):
    error: str
    details: str


async def search_twitter(args: ToolArguments) -> TwitterSearchResult | ToolError:
    """Search recent tweets about a topic."""
    query = str(args.get("query", "")).strip()
    max_results = min(int(args.get("max_results", 10) or 10), 100)

    settings = get_settings()
    token = getattr(settings, "x_bearer_token", "")
    if not token:
        return {"error": "X_BEARER_TOKEN not configured. Add it to .env and restart."}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            X_SEARCH_URL,
            params={
                "query": query,
                "max_results": max_results,
            },
            headers={"Authorization": "Bearer " + token},
        )

        if resp.status_code == 401:
            return {"error": "X API authentication failed. Check X_BEARER_TOKEN."}
        if resp.status_code == 429:
            return {"error": "X API rate limit exceeded. Try again later."}
        if resp.status_code == 400:
            return {"error": "X API rejected query. Try simpler search terms.", "details": resp.text}
        resp.raise_for_status()
        data = cast(dict[str, object], resp.json())

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
