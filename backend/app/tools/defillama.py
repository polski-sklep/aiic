from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Canonical baseline facts
#
# Not a tool. `agents/reconciliation.build_case_context` calls this once per
# evaluation, before any agent runs, to put deterministically-fetchable DeFi
# figures into the shared baseline every agent is handed.
#
# WHY IT DOES NOT USE get_tvl / get_protocol_fees
#
# `get_tvl` fetches `/protocol/{slug}`, which carries the protocol's entire
# daily TVL history: 18 MB for Pendle, 11 MB for Morpho, 10 MB for Aave, all to
# read one current number. That is the right trade for an agent that wants the
# 7-day trend; it is the wrong one for a baseline fetched on every run. The
# three endpoints below return 0.4-16 KB each and are enough for the baseline:
#
#   /config/smol/{slug}  — identity only (name, gecko_id, symbol). Confirms the
#                          slug we guessed is the protocol we mean.
#   /tvl/{slug}          — a bare number, nothing else.
#   /summary/fees/{slug} — with the chart series excluded.
#
# WHAT IS DELIBERATELY ABSENT: CATEGORY / MARKET SHARE
#
# Share of a category is the figure that produced the defect this module exists
# to fix, and it is the one figure here we must not synthesise. Two independent
# reasons, both verified against the live API on 2026-08-26:
#
# 1. The perpetuals dimension is not on the keyless API. Both
#    `GET /summary/derivatives/{slug}` and `GET /overview/derivatives` answer
#    `HTTP 402 "Upgrade to the paid API plan"`. The exact quantity in dispute —
#    perp volume, whose denominator any share of perp volume needs — cannot be
#    fetched at all without a subscription.
# 2. Even where a dimension *is* free (`/overview/dexs`), "share" is not a
#    published number but an arithmetic result whose value is decided by a
#    denominator we would be choosing: perp DEXs only, all DEXs, or DEXs plus
#    centralised venues. The two irreconcilable figures in the reports that
#    prompted this work — ~44% and 70-80% — are most likely two different
#    denominators rather than one of them being false.
#
# A canonical figure is treated by every agent as ground truth. Publishing one
# we computed from a peer set we picked ourselves would convert a contested
# estimate into an unchallengeable one. So share stays out of the metrics, and
# `_rule` (see reconciliation.py) tells agents in as many words that any figure
# absent from the baseline is uncanonical and needs a date and a named source.
# ---------------------------------------------------------------------------

#: Endpoints used for the baseline. Kept separate from BASE_URL usage above so
#: the deliberately-light choice is visible in one place.
CANONICAL_TIMEOUT = 12.0

_SLUG_STRIP = re.compile(r"[^a-z0-9-]+")


class CanonicalDefiFacts(TypedDict, total=False):
    """Deterministically-fetched DeFi figures for one project.

    Every key is optional and, critically, **absent when not retrieved**. A
    caller must never see 0.0 for "we did not fetch this": DeFiLlama answers
    `GET /tvl/plasma` with HTTP 200 and a zero-byte body, and `float(text or 0)`
    would turn "this protocol publishes no TVL" into "this protocol has no
    value locked".
    """

    slug: str
    name: str
    tvl_usd: float
    fees_30d_usd: float
    revenue_30d_usd: float
    fetched_at: str
    unavailable: str


def _slugify(value: str) -> str:
    return _SLUG_STRIP.sub("-", value.lower().strip().replace(" ", "-")).strip("-")


def _slug_candidates(project_name: str, coingecko_id: str, slug_hint: str) -> list[str]:
    """Slugs to try, best evidence first, deduplicated and order-preserving."""
    ordered = [_slugify(slug_hint), _slugify(coingecko_id), _slugify(project_name)]
    return [slug for slug in dict.fromkeys(ordered) if slug]


async def _resolve_slug(
    client: httpx.AsyncClient, project_name: str, coingecko_id: str, slug_hint: str
) -> tuple[str, dict[str, JSONValue]] | str:
    """Find the DeFiLlama slug for this project, or say why we could not.

    Returns ``(slug, identity)`` on success and a human-readable reason string
    on failure. Guessing a slug from a project name is cheap and usually right,
    but "usually" is not good enough for a figure every agent will treat as
    ground truth — Plasma, Morpho and Ethena each have four or more DeFiLlama
    entries whose names contain the project name and whose TVLs differ by three
    orders of magnitude. So a guessed slug is accepted only against positive
    identity evidence from `/config/smol/{slug}`:

    * its ``gecko_id`` equals the CoinGecko id we already resolved, or
    * we have no CoinGecko id and its ``name`` equals the project name.

    An explicit ``defillama_slug`` in project_info is an operator assertion and
    is trusted as given. Anything else is rejected, and the reason is reported
    rather than swallowed, because a wrong canonical figure is worse than none.
    """
    hint = _slugify(slug_hint)
    wanted_gecko = _slugify(coingecko_id)
    wanted_name = project_name.lower().strip()
    tried: list[str] = []

    for slug in _slug_candidates(project_name, coingecko_id, slug_hint):
        tried.append(slug)
        try:
            resp = await client.get(f"{BASE_URL}/config/smol/{slug}")
        except httpx.HTTPError as exc:
            return f"DeFiLlama could not be reached ({type(exc).__name__})"
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            return f"DeFiLlama returned HTTP {resp.status_code} while resolving '{slug}'"
        try:
            identity = cast(dict[str, JSONValue], resp.json())
        except ValueError:
            continue
        if not isinstance(identity, dict):
            continue

        if slug == hint:
            return slug, identity

        gecko = _slugify(str(identity.get("gecko_id") or ""))
        name = str(identity.get("name") or "").lower().strip()
        if wanted_gecko:
            if gecko == wanted_gecko:
                return slug, identity
            return (
                f"DeFiLlama '{slug}' maps to CoinGecko id "
                f"'{gecko or 'none'}', not '{wanted_gecko}' — not treated as the "
                f"same protocol"
            )
        if name and name == wanted_name:
            return slug, identity
        return f"DeFiLlama '{slug}' is '{identity.get('name')}', which we could not confirm is {project_name}"

    return f"no DeFiLlama protocol matched {tried or [project_name]}"


async def _fetch_tvl_number(client: httpx.AsyncClient, slug: str) -> float | None:
    """Current total TVL, or None when DeFiLlama publishes none for this slug.

    `/tvl/{slug}` answers with a bare number in the body. Three outcomes have to
    stay apart: a number, "listed but no TVL series" (HTTP 200, empty body —
    this is what Plasma and GEODNET return), and "no such slug" (HTTP 400,
    body ``Protocol not found``). Only the first is a figure.
    """
    try:
        resp = await client.get(f"{BASE_URL}/tvl/{slug}")
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    text = resp.text.strip().strip('"')
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


async def _fetch_fee_total_30d(
    client: httpx.AsyncClient, slug: str, data_type: str
) -> float | None:
    """Trailing-30-day fees or revenue, or None when there is no such series."""
    try:
        resp = await client.get(
            f"{BASE_URL}/summary/fees/{slug}",
            params={
                "dataType": data_type,
                "excludeTotalDataChart": "true",
                "excludeTotalDataChartBreakdown": "true",
            },
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = cast(dict[str, JSONValue], resp.json())
    except ValueError:
        return None
    total = data.get("total30d") if isinstance(data, dict) else None
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None
    return float(total)


async def fetch_canonical_facts(
    project_name: str, coingecko_id: str = "", slug_hint: str = ""
) -> CanonicalDefiFacts:
    """Fetch the DeFiLlama half of the canonical baseline for one project.

    Never raises and never guesses. On any failure the numeric keys are simply
    absent and ``unavailable`` carries the reason, so the caller can tell "we
    did not fetch this" from "this is zero" and say so to the agents.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=CANONICAL_TIMEOUT) as client:
            resolution = await _resolve_slug(client, project_name, coingecko_id, slug_hint)
            if isinstance(resolution, str):
                return {"fetched_at": fetched_at, "unavailable": resolution}
            slug, identity = resolution

            tvl, fees_30d, revenue_30d = await asyncio.gather(
                _fetch_tvl_number(client, slug),
                _fetch_fee_total_30d(client, slug, "dailyFees"),
                _fetch_fee_total_30d(client, slug, "dailyRevenue"),
            )
    except httpx.HTTPError as exc:
        return {
            "fetched_at": fetched_at,
            "unavailable": f"DeFiLlama could not be reached ({type(exc).__name__})",
        }

    facts: CanonicalDefiFacts = {
        "slug": slug,
        "name": str(identity.get("name") or project_name),
        "fetched_at": fetched_at,
    }
    if tvl is not None:
        facts["tvl_usd"] = tvl
    if fees_30d is not None:
        facts["fees_30d_usd"] = fees_30d
    if revenue_30d is not None:
        facts["revenue_30d_usd"] = revenue_30d

    missing = [
        label
        for label, value in (
            ("TVL", tvl),
            ("fees", fees_30d),
            ("revenue", revenue_30d),
        )
        if value is None
    ]
    if missing:
        facts["unavailable"] = (
            f"DeFiLlama publishes no {', '.join(missing)} for protocol '{slug}'"
        )
    return facts


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
