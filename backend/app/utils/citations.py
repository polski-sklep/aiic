from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import cast

from app.utils.types import FootnoteRecord, SourceRecord, ToolArguments, ToolResult


INLINE_CITATION_RE = re.compile(r"\[(\d+)\]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shorten(text: str, limit: int = 220) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _normalize_url(url: object) -> str:
    if not url:
        return ""
    value = str(url).strip()
    if value.startswith("@"):
        return f"https://x.com/{value[1:]}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("x.com/") or value.startswith("twitter.com/"):
        return "https://" + value
    return value


def make_source(
    *,
    label: str,
    url: str,
    kind: str = "web",
    tool_name: str = "",
    supports: str = "",
    agent_name: str = "",
    retrieved_at: str | None = None,
) -> SourceRecord | None:
    normalized_url = _normalize_url(url)
    if not normalized_url:
        return None
    return {
        "label": (label or normalized_url).strip(),
        "url": normalized_url,
        "kind": (kind or "web").strip(),
        "tool_name": tool_name,
        "agent_name": agent_name,
        "supports": _shorten(supports),
        "retrieved_at": retrieved_at or _now_iso(),
    }


def dedupe_sources(sources: list[SourceRecord]) -> list[SourceRecord]:
    deduped: list[SourceRecord] = []
    seen: dict[str, int] = {}

    for source in sources:
        if not isinstance(source, dict):
            continue
        url = _normalize_url(source.get("url"))
        if not url:
            continue
        key = url.lower()
        source = cast(SourceRecord, dict(source))
        source["url"] = url

        existing_index = seen.get(key)
        if existing_index is None:
            seen[key] = len(deduped)
            deduped.append(source)
            continue

        existing = deduped[existing_index]
        if not existing.get("supports") and source.get("supports"):
            existing["supports"] = source["supports"]
        if not existing.get("label") and source.get("label"):
            existing["label"] = source["label"]
        if not existing.get("agent_name") and source.get("agent_name"):
            existing["agent_name"] = source["agent_name"]
        if not existing.get("tool_name") and source.get("tool_name"):
            existing["tool_name"] = source["tool_name"]

    return deduped


def extract_sources_from_tool_result(
    tool_name: str,
    arguments: ToolArguments,
    result: ToolResult,
    *,
    agent_name: str = "",
) -> list[SourceRecord]:
    if not isinstance(result, dict) or result.get("error"):
        return []

    extracted: list[SourceRecord] = []
    retrieved_at = _now_iso()

    if tool_name == "web_search":
        for item in result.get("results", []):
            source = make_source(
                label=item.get("title") or f"Web result for {result.get('query', '')}",
                url=item.get("url", ""),
                kind="web_search",
                tool_name=tool_name,
                agent_name=agent_name,
                supports=item.get("description", ""),
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    elif tool_name == "search_twitter":
        for tweet in result.get("tweets", []):
            tweet_id = tweet.get("id", "").strip()
            if not tweet_id:
                continue
            source = make_source(
                label=f"X post {tweet_id}",
                url=f"https://x.com/i/web/status/{tweet_id}",
                kind="tweet",
                tool_name=tool_name,
                agent_name=agent_name,
                supports=tweet.get("text", ""),
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    elif tool_name in {"get_price", "get_token_info"}:
        coin_id = (result.get("coin_id") or arguments.get("coin_id") or "").strip().lower()
        if coin_id:
            source = make_source(
                label=f"CoinGecko: {coin_id}",
                url=f"https://www.coingecko.com/en/coins/{coin_id}",
                kind="market_data",
                tool_name=tool_name,
                agent_name=agent_name,
                supports="Market data, supply, valuation, liquidity, and historical token metrics.",
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    elif tool_name == "get_tvl":
        slug = (result.get("slug") or arguments.get("protocol") or "").strip().lower()
        if slug:
            source = make_source(
                label=f"DeFiLlama protocol page: {result.get('protocol', slug)}",
                url=f"https://defillama.com/protocol/{slug}",
                kind="tvl_data",
                tool_name=tool_name,
                agent_name=agent_name,
                supports="TVL, chain footprint, protocol metadata, and audit references.",
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)
        if result.get("url"):
            source = make_source(
                label=f"Official site: {result.get('protocol', slug)}",
                url=result.get("url", ""),
                kind="official_site",
                tool_name=tool_name,
                agent_name=agent_name,
                supports=result.get("description", ""),
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)
        if result.get("twitter"):
            source = make_source(
                label=f"Official X account: {result.get('protocol', slug)}",
                url=result.get("twitter", ""),
                kind="official_social",
                tool_name=tool_name,
                agent_name=agent_name,
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)
        for audit_link in result.get("audit_links", []):
            source = make_source(
                label=f"Audit report: {result.get('protocol', slug)}",
                url=audit_link,
                kind="audit",
                tool_name=tool_name,
                agent_name=agent_name,
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    elif tool_name == "get_protocol_fees":
        slug = (arguments.get("protocol") or result.get("protocol") or "").strip().lower()
        if slug:
            source = make_source(
                label=f"DeFiLlama fees page: {result.get('protocol', slug)}",
                url=f"https://defillama.com/fees/{slug}",
                kind="fees_data",
                tool_name=tool_name,
                agent_name=agent_name,
                supports="Protocol fees and revenue history.",
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    elif tool_name in {"search_notes", "read_note"}:
        results = result.get("results")
        if isinstance(results, list):
            for note in results:
                source = make_source(
                    label=note.get("title") or "Notion note",
                    url=note.get("url", ""),
                    kind="internal_note",
                    tool_name=tool_name,
                    agent_name=agent_name,
                    supports=str(note.get("properties", "")),
                    retrieved_at=retrieved_at,
                )
                if source:
                    extracted.append(source)
        else:
            source = make_source(
                label=result.get("title") or "Notion note",
                url=result.get("url", ""),
                kind="internal_note",
                tool_name=tool_name,
                agent_name=agent_name,
                supports=result.get("content", ""),
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

    return dedupe_sources(extracted)


def build_source_catalog(agent_results: dict[str, object], limit: int = 60) -> list[SourceRecord]:
    catalog: list[SourceRecord] = []

    for agent_name, result in agent_results.items():
        sources = getattr(result, "sources", None)
        if sources is None and isinstance(result, dict):
            sources = result.get("sources", [])
        if not isinstance(sources, list):
            continue

        for source in sources:
            if not isinstance(source, dict):
                continue
            item = cast(SourceRecord, dict(source))
            item["agent_name"] = item.get("agent_name") or agent_name
            catalog.append(item)

    return dedupe_sources(catalog)[:limit]


def format_source_catalog_text(sources: list[SourceRecord], limit: int = 40) -> str:
    if not sources:
        return "No source catalog available."

    lines: list[str] = []
    for idx, source in enumerate(sources[:limit], start=1):
        label = source.get("label") or source.get("url") or f"Source {idx}"
        url = source.get("url", "")
        agent = source.get("agent_name") or "unknown_agent"
        tool_name = source.get("tool_name") or "unknown_tool"
        supports = source.get("supports", "")
        line = f"{idx}. [{agent} via {tool_name}] {label} — {url}"
        if supports:
            line += f" | Supports: {supports}"
        lines.append(line)
    return "\n".join(lines)


def normalize_footnotes(raw_footnotes: object) -> list[FootnoteRecord]:
    if not isinstance(raw_footnotes, list):
        return []

    normalized: list[FootnoteRecord] = []
    for item in raw_footnotes:
        if not isinstance(item, dict):
            continue
        try:
            local_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue

        url = _normalize_url(item.get("url"))
        if not url:
            continue

        normalized.append(
            {
                "id": local_id,
                "label": (item.get("label") or url).strip(),
                "url": url,
                "kind": (item.get("kind") or "source").strip(),
                "supports": _shorten(item.get("supports", "")),
            }
        )

    normalized.sort(key=lambda item: item["id"])
    return normalized


def reindex_citations(
    text: str,
    footnotes: list[FootnoteRecord],
    merged: list[FootnoteRecord],
) -> tuple[str, list[FootnoteRecord]]:
    if not text or not footnotes:
        return text, merged

    mapping: dict[int, int] = {}
    existing_by_url = {item["url"].lower(): idx + 1 for idx, item in enumerate(merged)}

    for footnote in footnotes:
        url_key = footnote["url"].lower()
        global_id = existing_by_url.get(url_key)
        if global_id is None:
            global_id = len(merged) + 1
            merged.append(
                {
                    "id": global_id,
                    "label": footnote["label"],
                    "url": footnote["url"],
                    "kind": footnote.get("kind", "source"),
                    "supports": footnote.get("supports", ""),
                }
            )
            existing_by_url[url_key] = global_id
        else:
            existing = merged[global_id - 1]
            if not existing.get("supports") and footnote.get("supports"):
                existing["supports"] = footnote["supports"]

        mapping[footnote["id"]] = global_id

    def replace(match: re.Match[str]) -> str:
        try:
            local_id = int(match.group(1))
        except ValueError:
            return match.group(0)
        global_id = mapping.get(local_id)
        return f"[{global_id}]" if global_id is not None else match.group(0)

    return INLINE_CITATION_RE.sub(replace, text), merged
