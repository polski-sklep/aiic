from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from app.utils.types import FootnoteRecord, SourceRecord, ToolArguments, ToolResult


INLINE_CITATION_RE = re.compile(r"\[(\d+)\]")

#: What a citation marker becomes when it cannot be mapped to a source.
#:
#: An agent that writes "[1]" and supplies no matching footnote (QA-001), or
#: cites "[3]" having defined only two (QA-002), used to have its marker left
#: verbatim. The marker is then resolved against the *merged* footnote list, so
#: it points at whatever another agent happened to register in that slot — and
#: a dangling "[3]" turns into a perfectly valid reference to the wrong source
#: the moment a third source is merged.
#:
#: The marker is replaced rather than deleted. Deleting it would leave the
#: sentence reading as an ordinary unsourced assertion, indistinguishable from
#: prose that never claimed a source; the reader loses the fact that the agent
#: asserted evidence and failed to produce it. This token says so, carries no
#: number, and can never be resolved into a footnote by any later merge.
UNRESOLVED_CITATION = "[unverified]"

#: Characters that may follow a citation marker. A marker sits at the end of the
#: clause it supports: end of text, sentence punctuation, or a closing
#: delimiter. A bracketed integer followed by anything else is prose — "only [2]
#: of the five audits", "the top [10] holders" — and rewriting it would silently
#: change a stated quantity (QA-006).
_CITATION_TRAILERS = frozenset('.,;:!?)]}"\'”’\n\r')
_ADJACENT_MARKER_RE = re.compile(r"\s*\[\d+\]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shorten(text: str, limit: int = 220) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


#: A bare host, optionally with a path/query/fragment. Requires at least one dot
#: and no whitespace, which is what separates "docs.aave.com/governance" from
#: "N/A", "TBD", "none" and "internal knowledge".
_BARE_HOST_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
    r"(?:[:/?#].*)?$"
)

#: Handles are @name — letters, digits and underscore, nothing else.
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_]{1,30}$")


def _normalize_url(url: object) -> str:
    """Coerce a citation target to a URL, or to "" if it is not one.

    QA-004: anything non-empty used to survive, so a model writing
    ``"url": "N/A"`` or ``"internal knowledge"`` got a real entry in the source
    catalog — and because the dedupe key was the lowercased string, one agent's
    "N/A" and another's "n/a" merged into a single footnote. Two unrelated
    unsourced claims then shared one fabricated citation.
    """
    if not url:
        return ""
    value = str(url).strip()
    if not value:
        return ""
    if _HANDLE_RE.match(value):
        return f"https://x.com/{value[1:]}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if _BARE_HOST_RE.match(value):
        return "https://" + value
    return ""


def _dedupe_key(url: str) -> str:
    """Identity key for a URL.

    QA-005, both directions. Scheme and host are case-insensitive; the path is
    not, so lowercasing the whole URL merged ``/Aave/...`` with ``/aave/...``.
    A fragment names a position within one document and a trailing slash names
    the same resource, so neither may split one page into several footnotes.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.strip()
    if not parts.netloc:
        return url.strip()

    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


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
        key = _dedupe_key(url)
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


#: Keys that echo the request or describe the envelope rather than carrying data.
#: A result made only of these attests to nothing.
_ENVELOPE_METADATA_KEYS = frozenset(
    {
        "coin_id", "currency", "symbol", "ticker", "name", "slug", "protocol",
        "query", "database", "interval", "limit", "tool", "tool_name", "source",
        "status", "as_of", "date", "requested_at", "retrieved_at", "note",
        "data_status", "trading_pair",
    }
)


def _result_carries_data(result: Mapping[str, Any]) -> bool:
    """True if the result holds at least one datum, not just request echoes.

    QA-031: the only failure signal checked was ``result.get("error")``. A
    CoinGecko call that returns a full success envelope with every metric None —
    which is what an over-quota or unquoted-currency response produces — still
    had a CoinGecko source record attached, so the report cited CoinGecko as
    evidence for a number CoinGecko never returned. A fabricated citation is
    worse than a missing one: it survives review.
    """
    for key, value in result.items():
        if key in _ENVELOPE_METADATA_KEYS or key == "error":
            continue
        if value is None:
            continue
        if isinstance(value, (str, bytes, list, tuple, dict, set)) and len(value) == 0:
            continue
        return True
    return False


def extract_sources_from_tool_result(
    tool_name: str,
    arguments: ToolArguments,
    result: ToolResult,
    *,
    agent_name: str = "",
) -> list[SourceRecord]:
    if not isinstance(result, dict) or result.get("error"):
        return []
    if not _result_carries_data(result):
        return []

    extracted: list[SourceRecord] = []
    retrieved_at = _now_iso()

    explicit_sources = result.get("sources", [])
    if isinstance(explicit_sources, list):
        for item in explicit_sources:
            if not isinstance(item, dict):
                continue
            source = make_source(
                label=item.get("label") or item.get("url") or tool_name,
                url=item.get("url", ""),
                kind=item.get("kind", "source"),
                tool_name=tool_name,
                agent_name=agent_name,
                supports=item.get("supports", ""),
                retrieved_at=retrieved_at,
            )
            if source:
                extracted.append(source)

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
        local_id = _coerce_footnote_id(item.get("id"))
        if local_id is None:
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


def _coerce_footnote_id(raw: object) -> int | None:
    """Accept only ids that unambiguously name a footnote number.

    QA-009: ``int(raw)`` truncated 1.9 to 1 and True to 1, manufacturing
    duplicate ids out of distinct inputs — which then feeds QA-003, where a
    duplicate silently repoints the citation. A value that is not already a
    whole number is not a footnote id, so it is dropped rather than guessed at.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() and raw > 0 else None
    if isinstance(raw, str):
        text = raw.strip()
        if text.isdigit() and int(text) > 0:
            return int(text)
    return None


def _is_citation_position(text: str, end: int) -> bool:
    """True if the marker ending at ``end`` sits where a citation sits.

    QA-006: ``INLINE_CITATION_RE`` matches any bracketed integer, so prose like
    "only [2] of the five audits are public" had its *quantity* rewritten to
    whatever global id local footnote 2 mapped to. The reader saw "only [4] of
    the five audits" — a stated figure changed value, and a factual claim was
    corrupted, not merely mis-cited.

    A citation trails the clause it supports. Anything else is prose and is left
    exactly as the agent wrote it.
    """
    index = end
    while True:
        adjacent = _ADJACENT_MARKER_RE.match(text, index)
        if not adjacent:
            break
        index = adjacent.end()
    if index >= len(text):
        return True
    return text[index] in _CITATION_TRAILERS


def _renumber_merged(merged: list[FootnoteRecord]) -> list[FootnoteRecord]:
    """Restore ``merged[i]["id"] == i + 1``.

    QA-007: both the lookup (``idx + 1``) and the allocation (``len(merged) + 1``)
    assume that invariant, and nothing enforced it. A merged list arriving from a
    resumed render or a filtered list produced ids inconsistent with the numbers
    written into the prose. Making the function establish what it relies on is
    cheaper than making every caller promise it.
    """
    cleaned: list[FootnoteRecord] = []
    for item in merged:
        if not isinstance(item, dict):
            continue
        url = _normalize_url(item.get("url"))
        if not url:
            continue
        entry = cast(FootnoteRecord, dict(item))
        entry["url"] = url
        entry["id"] = len(cleaned) + 1
        cleaned.append(entry)

    merged[:] = cleaned
    return merged


def reindex_citations(
    text: str,
    footnotes: list[FootnoteRecord],
    merged: list[FootnoteRecord],
) -> tuple[str, list[FootnoteRecord]]:
    """Rewrite one agent's local citation numbers into the merged id space.

    Every ``[N]`` in ``text`` that sits in a citation position ends up as either
    a correct global id or :data:`UNRESOLVED_CITATION`. It is never left as a
    number the agent did not earn: an unmapped marker resolves against whatever
    another agent registered in that slot, and a dangling one becomes a valid
    reference to the wrong source as soon as the merged list grows past it
    (QA-001, QA-002).
    """
    if not text:
        return text, merged

    # QA-008: only output from normalize_footnotes was safe to index into.
    # Normalising here is idempotent and makes every caller safe.
    footnotes = normalize_footnotes(footnotes)
    merged = _renumber_merged(merged)

    mapping: dict[int, int] = {}
    existing_by_url = {_dedupe_key(item["url"]): item["id"] for item in merged}

    for footnote in footnotes:
        url_key = _dedupe_key(footnote["url"])
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

        # QA-003: this was an assignment, so two footnotes both numbered 1 left
        # every [1] in the prose pointing at the second URL while the first was
        # registered and orphaned. First definition wins: it is the one the
        # agent was writing about when it first used the marker.
        mapping.setdefault(footnote["id"], global_id)

    def replace(match: re.Match[str]) -> str:
        if not _is_citation_position(text, match.end()):
            return match.group(0)
        global_id = mapping.get(int(match.group(1)))
        return f"[{global_id}]" if global_id is not None else UNRESOLVED_CITATION

    return INLINE_CITATION_RE.sub(replace, text), merged
