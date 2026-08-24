from __future__ import annotations
from typing import TypedDict, cast

import httpx

from app.llm import JSONValue, SourceReference, ToolDefinition
from app.tools.contracts import ToolRegistrar
from app.tools.http_errors import (
    BAD_REQUEST,
    NOT_FOUND,
    NO_DATA,
    ToolFailure,
    http_failure,
    tool_failure,
    transport_failure,
)
from app.utils.types import ToolArguments


BASE_URL = "https://api.llama.fi"

SERVICE = "DeFiLlama"

ToolError = ToolFailure


class TVLPoint(TypedDict):
    date: JSONValue
    tvl: JSONValue


class ProtocolTvlResult(TypedDict):
    protocol: JSONValue
    slug: JSONValue
    category: JSONValue
    chains: list[JSONValue]
    current_tvl_total: JSONValue
    tvl_by_chain: dict[str, JSONValue]
    tvl_trend_7d: list[TVLPoint]
    description: str
    url: JSONValue
    twitter: JSONValue
    audit_links: list[JSONValue]
    oracles: list[JSONValue]
    sources: list[SourceReference]


class ProtocolFeesResult(TypedDict):
    protocol: JSONValue
    total_24h: JSONValue
    total_48h_to_24h: JSONValue
    total_7d: JSONValue
    total_30d: JSONValue
    total_all_time: JSONValue
    chains: list[str]
    sources: list[SourceReference]


def _source(label: str, url: str, kind: str) -> SourceReference:
    return cast(
        SourceReference,
        {
            "label": label,
            "url": url,
            "kind": kind,
        },
    )


def _protocol_arg(raw: object) -> str:
    if raw is None:
        return ""
    return str(raw).lower().strip()


async def get_tvl(args: ToolArguments) -> ProtocolTvlResult | ToolError:
    """Fetch current TVL for a protocol.

    QA-029: this was the primary DeFi metric tool and the only one of the eleven
    with no status handling at all. A 404, a 429 and a DeFiLlama outage all left
    as httpx exceptions and reached the agent as the same generic registry
    string. "This protocol is not on DeFiLlama" is a finding; "DeFiLlama is
    down" is a data gap; an agent that cannot separate them will assert a
    protocol has no TVL because the API was unavailable.
    """
    protocol = _protocol_arg(args.get("protocol"))
    if not protocol:
        return tool_failure(
            BAD_REQUEST, "No protocol was supplied. Pass a DeFiLlama slug (e.g. 'aave')."
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE_URL}/protocol/{protocol}")
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if resp.status_code == 404:
        return tool_failure(
            NOT_FOUND,
            f"'{protocol}' is not listed on {SERVICE}. Check the slug before concluding "
            f"the protocol has no TVL.",
        )
    if resp.status_code != 200:
        return http_failure(SERVICE, resp.status_code)

    try:
        data = cast(dict[str, JSONValue], resp.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

    current_tvl = cast(dict[str, JSONValue], data.get("currentChainTvls", {}))
    tvl_history = cast(list[dict[str, JSONValue]], data.get("tvl", []))
    recent_tvl = tvl_history[-7:] if tvl_history else []

    return {
        "protocol": data.get("name", protocol),
        "slug": data.get("slug"),
        "category": data.get("category"),
        "chains": cast(list[JSONValue], data.get("chains", [])),
        "current_tvl_total": sum(
            float(value) for value in current_tvl.values() if isinstance(value, (int, float))
        ) if current_tvl else None,
        "tvl_by_chain": current_tvl,
        "tvl_trend_7d": [
            {
                "date": point.get("date"),
                "tvl": point.get("totalLiquidityUSD"),
            }
            for point in recent_tvl
        ],
        "description": str(data.get("description", ""))[:300],
        "url": data.get("url"),
        "twitter": data.get("twitter"),
        "audit_links": cast(list[JSONValue], data.get("audit_links", [])),
        "oracles": cast(list[JSONValue], data.get("oracles", [])),
        "sources": [
            _source(
                f"DeFiLlama protocol page: {data.get('name', protocol)}",
                f"https://defillama.com/protocol/{data.get('slug') or protocol}",
                "tvl_data",
            )
        ],
    }


async def get_protocol_fees(args: ToolArguments) -> ProtocolFeesResult | ToolError:
    """Fetch fee and revenue data for a protocol."""
    protocol = _protocol_arg(args.get("protocol"))
    if not protocol:
        return tool_failure(
            BAD_REQUEST, "No protocol was supplied. Pass a DeFiLlama slug (e.g. 'aave')."
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BASE_URL}/summary/fees/{protocol}",
                params={"dataType": "dailyFees"},
            )
    except httpx.HTTPError as exc:
        return transport_failure(SERVICE, exc)

    if resp.status_code == 404:
        return tool_failure(NO_DATA, f"No fee data available for '{protocol}'")
    if resp.status_code != 200:
        return http_failure(SERVICE, resp.status_code)

    try:
        data = cast(dict[str, JSONValue], resp.json())
    except ValueError as exc:
        return transport_failure(SERVICE, exc)

    breakdown = data.get("totalDataChartBreakdown", [])
    last_breakdown = cast(dict[str, JSONValue], breakdown[-1]) if isinstance(breakdown, list) and breakdown else {}

    return {
        "protocol": data.get("name", protocol),
        "total_24h": data.get("total24h"),
        "total_48h_to_24h": data.get("total48hto24h"),
        "total_7d": data.get("total7d"),
        "total_30d": data.get("total30d"),
        "total_all_time": data.get("totalAllTime"),
        "chains": list(last_breakdown.keys()),
        "sources": [
            _source(
                f"DeFiLlama fees page: {data.get('name', protocol)}",
                f"https://defillama.com/fees/{protocol}",
                "fees_data",
            )
        ],
    }


def register(registry: ToolRegistrar) -> None:
    registry.register(
        ToolDefinition(
            name="get_tvl",
            description="Get current TVL, TVL by chain, 7-day TVL trend, category, audit info, and oracles for a DeFi protocol. Use DeFiLlama slug (e.g., 'aave', 'uniswap', 'lido').",
            parameters={
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "DeFiLlama protocol slug (e.g., 'aave', 'uniswap', 'lido')",
                    },
                },
                "required": ["protocol"],
            },
        ),
        get_tvl,
    )

    registry.register(
        ToolDefinition(
            name="get_protocol_fees",
            description="Get fee and revenue data (24h, 7d, 30d, all-time) for a DeFi protocol.",
            parameters={
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "DeFiLlama protocol slug",
                    },
                },
                "required": ["protocol"],
            },
        ),
        get_protocol_fees,
    )
