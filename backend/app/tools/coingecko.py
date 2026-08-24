from __future__ import annotations

import asyncio
from typing import TypedDict, cast

import httpx

from app.config import get_settings
from app.llm import JSONValue, SourceReference, ToolDefinition
from app.tools.contracts import ToolRegistrar
from app.tools.http_errors import (
    NOT_FOUND,
    NO_DATA,
    RATE_LIMITED,
    ToolFailure,
    body_rate_limited,
    http_failure,
    tool_failure,
    transport_failure,
)
from app.utils.types import ToolArguments


BASE_URL = "https://api.coingecko.com/api/v3"
RETRY_DELAYS_SECONDS = (2, 4, 8, 16)

SERVICE = "CoinGecko"

#: Kept as a module-level name because six months of call sites annotate with it.
ToolError = ToolFailure


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


def _is_body_rate_limit(response: httpx.Response) -> bool:
    """True if a 200 response is really an over-quota answer."""
    if response.status_code != 200:
        return False
    try:
        return body_rate_limited(response.json())
    except ValueError:
        return False


async def _get_with_backoff(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str],
    retry_body_429: bool = False,
) -> httpx.Response | None:
    """GET with the documented 429 ladder. ``None`` means the quota never cleared.

    ``retry_body_429`` extends the ladder to CoinGecko's free-tier body-level
    429 — HTTP 200 carrying ``{"status": {"error_code": 429}}``. It is the same
    quota, so it deserves the same ladder, and without it the loop exits on the
    first attempt and the body is parsed as data (QA-042).

    It is opt-in because ``app.knowledge.calibration.fetch_price_on`` layers its
    own, much longer body-429 ladder (20/40/60s) on top of this function and
    needs the rate-limited response handed back to it to do so. Defaulting to
    True would silently shorten calibration's retry window on the only ledger
    this project has.
    """
    url = f"{BASE_URL}/{path.lstrip('/')}"

    for attempt, delay in enumerate((0, *RETRY_DELAYS_SECONDS), start=1):
        if delay:
            await asyncio.sleep(delay)

        response = await client.get(url, params=params, headers=_headers())
        if response.status_code == 429:
            continue
        if retry_body_429 and _is_body_rate_limit(response):
            continue
        return response

    return None


def _clean_arg(raw: object, default: str = "") -> str:
    """Read a string argument without inventing one.

    QA-032: ``str(args.get("coin_id", ""))`` only applies its default when the
    key is *absent*. A model emitting ``{"coin_id": null}`` produced the literal
    string "none", which was then sent to CoinGecko as a real query — burning a
    call against the quota and returning "Coin 'none' not found", an error
    naming a coin nobody asked about.
    """
    if raw is None:
        return default
    text = str(raw).strip().lower()
    return text or default


async def get_price(args: ToolArguments) -> CoinGeckoPriceResult | ToolError:
    """Fetch current price, market cap, and volume for a token."""
    coin_id = _clean_arg(args.get("coin_id"))
    currency = _clean_arg(args.get("currency"), "usd")

    if not coin_id:
        return tool_failure(
            "bad_request",
            "No coin_id was supplied. Pass a CoinGecko coin ID (e.g. 'bitcoin', 'aave').",
        )

    try:
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
                retry_body_429=True,
            )
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if response is None:
        return tool_failure(
            RATE_LIMITED,
            f"{SERVICE} rate limit persisted after retries. Try again shortly.",
        )

    if response.status_code != 200:
        return http_failure(SERVICE, response.status_code)

    try:
        data = cast(dict[str, JSONValue], response.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

    if coin_id not in data:
        return tool_failure(
            NOT_FOUND,
            f"Coin '{coin_id}' not found. Use CoinGecko coin ID (e.g., 'bitcoin', 'ethereum', 'uniswap').",
        )

    coin_data = cast(dict[str, JSONValue], data[coin_id])
    price = coin_data.get(currency)
    if price is None:
        # QA-031: this used to return a full envelope with every field None and
        # a CoinGecko source attached, so the report cited CoinGecko as evidence
        # for a price CoinGecko never quoted.
        return tool_failure(
            NO_DATA,
            f"{SERVICE} has no {currency.upper()} quote for '{coin_id}'. "
            f"The coin exists; the requested currency pair does not. Try currency 'usd'.",
        )

    return {
        "coin_id": coin_id,
        "price": price,
        "market_cap": coin_data.get(f"{currency}_market_cap"),
        "volume_24h": coin_data.get(f"{currency}_24h_vol"),
        "change_24h_pct": coin_data.get(f"{currency}_24h_change"),
        "currency": currency,
        "sources": [_source(coin_id)],
    }


async def get_token_info(args: ToolArguments) -> CoinGeckoTokenInfoResult | ToolError:
    """Fetch detailed token information including supply data."""
    coin_id = _clean_arg(args.get("coin_id"))

    if not coin_id:
        return tool_failure(
            "bad_request",
            "No coin_id was supplied. Pass a CoinGecko coin ID (e.g. 'bitcoin', 'aave').",
        )

    try:
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
                retry_body_429=True,
            )
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if response is None:
        # QA-042, the worse half: with no body-429 guard this path returned a
        # complete success envelope with every metric null and a CoinGecko
        # source attached. Supply, FDV and genesis_date all arrived as null with
        # no error anywhere — and a null genesis_date is exactly what makes the
        # structural gate skip its minimum-age check.
        return tool_failure(
            RATE_LIMITED,
            f"{SERVICE} rate limit persisted after retries. Try again shortly.",
        )
    if response.status_code == 404:
        return tool_failure(NOT_FOUND, f"Coin '{coin_id}' not found on {SERVICE}.")

    if response.status_code != 200:
        return http_failure(SERVICE, response.status_code)

    try:
        data = cast(dict[str, JSONValue], response.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

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


def register(registry: ToolRegistrar) -> None:
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
