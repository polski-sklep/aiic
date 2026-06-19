from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypedDict, cast

import httpx

from app.config import get_settings
from app.llm import JSONValue, SourceReference, ToolDefinition
from app.tools.registry import ToolArguments

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

BASE_URL = "https://api.coingecko.com/api/v3"
RETRY_DELAYS_SECONDS = (2, 4, 8, 16)


class ToolError(TypedDict, total=False):
    error: str
    details: str


class CoinGeckoPriceResult(TypedDict):
    coin_id: str
    price: JSONValue
    market_cap: JSONValue
    volume_24h: JSONValue
    change_24h_pct: JSONValue
    currency: str
    sources: list[SourceReference]


class CoinGeckoTokenInfoResult(TypedDict):
    coin_id: str
    name: JSONValue
    symbol: JSONValue
    categories: list[JSONValue]
    description: str
    market_cap_rank: JSONValue
    current_price_usd: JSONValue
    market_cap_usd: JSONValue
    fully_diluted_valuation: JSONValue
    total_volume_usd: JSONValue
    circulating_supply: JSONValue
    total_supply: JSONValue
    max_supply: JSONValue
    ath_usd: JSONValue
    ath_change_pct: JSONValue
    atl_usd: JSONValue
    genesis_date: JSONValue
    developer_score: JSONValue
    community_score: JSONValue
    liquidity_score: JSONValue
    sources: list[SourceReference]


def _source(coin_id: str) -> SourceReference:
    return cast(
        SourceReference,
        {
            "label": f"CoinGecko: {coin_id}",
            "url": f"https://www.coingecko.com/en/coins/{coin_id}",
            "kind": "market_data",
            "supports": "CoinGecko market data, supply, valuation, liquidity, and historical token metrics.",
        },
    )


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.coingecko_api_key:
        return {}
    return {"x-cg-demo-api-key": settings.coingecko_api_key}


async def _get_with_backoff(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str],
) -> httpx.Response | None:
    url = f"{BASE_URL}/{path.lstrip('/')}"

    for attempt, delay in enumerate((0, *RETRY_DELAYS_SECONDS), start=1):
        if delay:
            await asyncio.sleep(delay)

        response = await client.get(url, params=params, headers=_headers())
        if response.status_code != 429:
            return response

    return None


async def get_price(args: ToolArguments) -> CoinGeckoPriceResult | ToolError:
    """Fetch current price, market cap, and volume for a token."""
    coin_id = str(args.get("coin_id", "")).lower().strip()
    currency = str(args.get("currency", "usd")).lower()

    async with httpx.AsyncClient(timeout=15) as client:
        response = await _get_with_backoff(
            client,
            "simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": currency,
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )

    if response is None:
        return {"error": "CoinGecko rate limit persisted after retries. Try again shortly."}

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {"error": f"CoinGecko request failed with status {exc.response.status_code}."}

    data = cast(dict[str, JSONValue], response.json())
    if coin_id not in data:
        return {"error": f"Coin '{coin_id}' not found. Use CoinGecko coin ID (e.g., 'bitcoin', 'ethereum', 'uniswap')."}

    coin_data = cast(dict[str, JSONValue], data[coin_id])
    return {
        "coin_id": coin_id,
        "price": coin_data.get(currency),
        "market_cap": coin_data.get(f"{currency}_market_cap"),
        "volume_24h": coin_data.get(f"{currency}_24h_vol"),
        "change_24h_pct": coin_data.get(f"{currency}_24h_change"),
        "currency": currency,
        "sources": [_source(coin_id)],
    }


async def get_token_info(args: ToolArguments) -> CoinGeckoTokenInfoResult | ToolError:
    """Fetch detailed token information including supply data."""
    coin_id = str(args.get("coin_id", "")).lower().strip()

    async with httpx.AsyncClient(timeout=15) as client:
        response = await _get_with_backoff(
            client,
            f"coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "true",
                "developer_data": "true",
            },
        )

    if response is None:
        return {"error": "CoinGecko rate limit persisted after retries. Try again shortly."}
    if response.status_code == 404:
        return {"error": f"Coin '{coin_id}' not found on CoinGecko."}

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {"error": f"CoinGecko request failed with status {exc.response.status_code}."}

    data = cast(dict[str, JSONValue], response.json())
    market = cast(dict[str, JSONValue], data.get("market_data", {}))
    description = cast(dict[str, JSONValue], data.get("description", {}))
    return {
        "coin_id": coin_id,
        "name": data.get("name"),
        "symbol": data.get("symbol"),
        "categories": cast(list[JSONValue], data.get("categories", [])),
        "description": str(description.get("en", ""))[:500],
        "market_cap_rank": data.get("market_cap_rank"),
        "current_price_usd": cast(dict[str, JSONValue], market.get("current_price", {})).get("usd"),
        "market_cap_usd": cast(dict[str, JSONValue], market.get("market_cap", {})).get("usd"),
        "fully_diluted_valuation": cast(dict[str, JSONValue], market.get("fully_diluted_valuation", {})).get("usd"),
        "total_volume_usd": cast(dict[str, JSONValue], market.get("total_volume", {})).get("usd"),
        "circulating_supply": market.get("circulating_supply"),
        "total_supply": market.get("total_supply"),
        "max_supply": market.get("max_supply"),
        "ath_usd": cast(dict[str, JSONValue], market.get("ath", {})).get("usd"),
        "ath_change_pct": cast(dict[str, JSONValue], market.get("ath_change_percentage", {})).get("usd"),
        "atl_usd": cast(dict[str, JSONValue], market.get("atl", {})).get("usd"),
        "genesis_date": data.get("genesis_date"),
        "developer_score": data.get("developer_score"),
        "community_score": data.get("community_score"),
        "liquidity_score": data.get("liquidity_score"),
        "sources": [_source(coin_id)],
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="get_price",
            description="Get current price, market cap, 24h volume, and 24h price change for a cryptocurrency. Use CoinGecko coin IDs (e.g., 'bitcoin', 'ethereum', 'chainlink').",
            parameters={
                "type": "object",
                "properties": {
                    "coin_id": {
                        "type": "string",
                        "description": "CoinGecko coin ID (e.g., 'bitcoin', 'ethereum', 'uniswap')",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Target currency (default: usd)",
                        "default": "usd",
                    },
                },
                "required": ["coin_id"],
            },
        ),
        get_price,
    )

    registry.register(
        ToolDefinition(
            name="get_token_info",
            description="Get detailed token info: supply data (circulating, total, max), FDV, ATH/ATL, categories, developer/community scores. Use for tokenomics analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "coin_id": {
                        "type": "string",
                        "description": "CoinGecko coin ID",
                    },
                },
                "required": ["coin_id"],
            },
        ),
        get_token_info,
    )
