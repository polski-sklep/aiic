"""Data reconciliation - shared case context and numerical consistency checks."""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.utils.types import JSONObject

#: Leaf names that make a number worth reconciling, matched against the key with
#: separators removed so "market_cap", "marketCap" and "marketcap" all hit.
METRIC_TERMS = ("tvl", "supply", "revenue", "marketcap", "fdv")

#: Two agents must differ by more than this fraction to be flagged.
DIVERGENCE_THRESHOLD = 0.2


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """QA-023: the same explicit-None defect as QA-015, one step earlier.

    build_case_context runs before any agent does, so an AttributeError here
    aborts the whole evaluation rather than degrading to an unknown-metrics
    baseline.
    """
    return value if isinstance(value, Mapping) else {}


#: What an agent is obliged to do with the baseline, rendered to the model as
#: part of the metrics block.
#:
#: WHY THIS STRING LIVES INSIDE ``canonical_metrics``
#:
#: ``BaseAgent.get_system_prompt`` renders exactly two things from the case
#: context — ``canonical_metrics`` and ``case_time``. ``data_snapshot_note`` has
#: never reached a model. ``base.py`` is owned by another branch, so the only
#: channel this module controls that an agent actually reads is the metrics dict
#: itself. The leading underscore keeps it sorted ahead of the figures under
#: ``json.dumps(..., sort_keys=True)`` and marks it as not-a-metric.
#:
#: The obligation is deliberately stronger than the "flag discrepancies" heading
#: in base.py. Flagging is optional and unowned; departing on the record is not.
#: The final sentence is the part aimed squarely at the defect: the GMX report
#: built a decision trigger ("a material loss of its 70-80% dominance would
#: upgrade the call") on a category-share figure that no source had made
#: canonical and that the Hyperliquid report, seventeen minutes earlier, had put
#: at ~44%. A trigger anchored to an undated, unsourced number can never fire
#: correctly.
BASELINE_RULE = (
    "Canonical figures for this evaluation, dated in _as_of. Use them as given. If a "
    "source you find disagrees materially, do not silently substitute it: record the "
    "departure in key_findings — canonical figure, your figure, your source, your "
    "source's own as-of date, and why. Any quantity absent here (market or category "
    "share, peer or sector volumes, rankings) is NOT canonical: quote it only with an "
    "explicit as-of date and a named source, and never anchor a threshold, signpost or "
    "trigger on one that lacks them."
)


def _stamp(moment: datetime) -> str:
    """Second-precision UTC. Microseconds are four tokens of noise per stamp."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trim_stamp(raw: object) -> str:
    text = str(raw or "")
    try:
        return _stamp(datetime.fromisoformat(text))
    except ValueError:
        return text or "unknown"


def _defillama_clause(defi_facts: Mapping[str, Any]) -> str:
    """One clause saying what DeFiLlama gave us, or why it gave us nothing."""
    if not defi_facts:
        return "tvl/fees/revenue: not fetched"
    slug = defi_facts.get("slug")
    unavailable = defi_facts.get("unavailable")
    if not slug:
        return f"tvl/fees/revenue: not fetched ({unavailable or 'no DeFiLlama match'})"
    retrieved = [
        label
        for label, key in (("tvl", "tvl_usd"), ("fees", "fees_30d_usd"), ("revenue", "revenue_30d_usd"))
        if key in defi_facts
    ]
    clause = (
        f"DeFiLlama '{slug}' {_trim_stamp(defi_facts.get('fetched_at'))} "
        f"({', '.join(retrieved) or 'nothing'}"
    )
    if unavailable:
        clause += f"; {unavailable} — absent, not zero"
    return clause + ")"


def build_case_context(
    project_name: str,
    resolved_info: JSONObject,
    defi_facts: JSONObject | None = None,
) -> JSONObject:
    """Build canonical case context from protocol resolution data.

    ``defi_facts`` is the result of ``tools.defillama.fetch_canonical_facts``,
    fetched by the orchestrator immediately before this call. It is a parameter
    rather than an await inside this function so the signature stays synchronous
    and every existing caller keeps working.

    Two properties the callers depend on:

    * **Absent, never zero.** A DeFiLlama figure we did not retrieve does not
      appear as a key at all, and ``_as_of`` says so in prose. ``0.0`` in this
      dict always means a real zero. The trap is concrete: DeFiLlama answers
      ``GET /tvl/plasma`` with HTTP 200 and an empty body, so the obvious
      ``float(body or 0)`` reports a chain with no TVL series as a chain with no
      value locked.
    * **Nothing extra when nothing is known.** ``_as_of`` and ``_rule`` are added
      only once at least one figure resolved. An all-null baseline is not an
      authority and must not be dressed as one.
    """
    now = datetime.now(timezone.utc)
    resolved_info = _as_mapping(resolved_info)
    price_data = _as_mapping(resolved_info.get("_price_data"))
    token_data = _as_mapping(resolved_info.get("_token_data"))
    defi = _as_mapping(defi_facts)

    metrics: dict[str, Any] = {
        "price_usd": price_data.get("price"),
        "market_cap_usd": price_data.get("market_cap") or token_data.get("market_cap_usd"),
        "volume_24h_usd": price_data.get("volume_24h") or token_data.get("total_volume_usd"),
        "fdv_usd": token_data.get("fully_diluted_valuation"),
        "circulating_supply": token_data.get("circulating_supply"),
        "total_supply": token_data.get("total_supply"),
        "max_supply": token_data.get("max_supply"),
    }
    coingecko_resolved = any(value is not None for value in metrics.values())

    for key in ("tvl_usd", "fees_30d_usd", "revenue_30d_usd"):
        if key in defi:
            metrics[key] = defi[key]

    if coingecko_resolved or any(k in defi for k in ("tvl_usd", "fees_30d_usd", "revenue_30d_usd")):
        metrics["_as_of"] = "; ".join((
            f"CoinGecko spot {_stamp(now)} (price, market cap, volume, supply)"
            if coingecko_resolved
            else "price/market cap/volume/supply: not fetched",
            _defillama_clause(defi),
        ))
        metrics["_rule"] = BASELINE_RULE

    return {
        "case_time": now.isoformat(),
        "project_name": project_name,
        "canonical_metrics": metrics,
        "evaluation_date": now.strftime("%Y-%m-%d"),
        "data_snapshot_note": "Canonical baseline metrics as of evaluation_date. Flag discrepancies with external sources.",
    }


async def fetch_canonical_defi_facts(
    project_name: str, resolved_info: JSONObject
) -> JSONObject:
    """Fetch the DeFiLlama half of the baseline for ``build_case_context``.

    A thin seam so the orchestrator does not have to know which module speaks to
    DeFiLlama. Imported lazily for the same reason ``orchestrator._resolve_protocol``
    imports the registry lazily: ``app.tools`` pulls in the whole tool package at
    import time.
    """
    from app.tools.defillama import fetch_canonical_facts

    resolved_info = _as_mapping(resolved_info)
    return dict(
        await fetch_canonical_facts(
            project_name,
            coingecko_id=str(resolved_info.get("coingecko_id") or ""),
            slug_hint=str(resolved_info.get("defillama_slug") or ""),
        )
    )


def reconcile_data(agent_outputs: dict[str, JSONObject], case_context: JSONObject) -> JSONObject:
    """Flag numerical inconsistencies across agent outputs."""
    inconsistencies = []
    metrics_by_agent = {
        agent_name: extracted
        for agent_name, output in agent_outputs.items()
        if isinstance(output, Mapping) and (extracted := _extract_metrics(output))
    }

    metric_values = {}
    for agent, metrics in metrics_by_agent.items():
        for key, val in metrics.items():
            metric_values.setdefault(_group_metric_key(key), []).append({"agent": agent, "key": key, "value": val})

    for metric_key, entries in metric_values.items():
        if len(entries) < 2:
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, b = entries[i], entries[j]
                if a["agent"] == b["agent"]:
                    # Reconciliation is a cross-agent check. Leaf grouping can
                    # put two of one agent's own figures in the same group
                    # (a per-chain breakdown); that is not a disagreement.
                    continue
                divergence = _relative_divergence(a["value"], b["value"])
                if divergence > DIVERGENCE_THRESHOLD:
                    inconsistencies.append({
                        "metric": metric_key,
                        "agent_a": a["agent"],
                        "value_a": a["value"],
                        "agent_b": b["agent"],
                        "value_b": b["value"],
                        "divergence_pct": round(divergence * 100, 1),
                    })

    return {
        "case_time": case_context.get("case_time"),
        "canonical_metrics": case_context.get("canonical_metrics", {}),
        "inconsistencies_found": len(inconsistencies),
        "inconsistencies": inconsistencies[:10],
        "status": "CLEAN" if not inconsistencies else "WARNING: %d inconsistencies" % len(inconsistencies),
    }


def _flatten(value: object, prefix: str = "") -> list[tuple[str, object]]:
    """Every scalar in a nested structure, with its path and its leaf name.

    QA-022: this descended into dicts only. A per-chain breakdown — the natural
    shape for TVL — was invisible to reconciliation entirely.
    """
    items: list[tuple[str, object]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (Mapping, list, tuple)):
                items.extend(_flatten(child, path))
            else:
                items.append((path, child))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(child, (Mapping, list, tuple)):
                items.extend(_flatten(child, path))
            else:
                items.append((path, child))
    return items


def _as_metric_number(value: object) -> float | None:
    """A positive finite number, or None.

    QA-021: ``isinstance(val, (int, float))`` is True for bool, so a flag like
    ``{"tvl_verified": true}`` was extracted as the number 1 and compared
    against another agent's real TVL — a 499,999,900% "inconsistency" that
    buries every real disagreement under the ten-item cap.

    QA-022: models routinely quote their figures, and ``"100"`` was ignored, so
    the check was silently skipped for any agent that did.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _leaf_name(path: str) -> str:
    """The final component of a flattened path, separators removed.

    QA-019: metrics were grouped by the *whole* dotted path, so two agents had
    to agree on the exact nesting and the exact spelling before anything was
    compared. The eight data agents are independent by design (CONTRACTS §4.2)
    and each shapes its own JSON, so in practice nothing was ever compared and
    reconcile_data reported CLEAN for every evaluation.

    Grouping on the leaf keeps genuinely different metrics apart —
    "total_supply" and "circulating_supply" remain distinct — while letting
    "metrics.tvl" and "protocol_data.tvl" meet. It will also group a nested
    comparison figure (a competitor's market cap) with a top-level one; that is
    a false positive in a report of *candidate* disagreements, and strictly
    better than the previous behaviour of never comparing anything.
    """
    leaf = path.rsplit(".", 1)[-1]
    leaf = leaf.split("[", 1)[0]
    return leaf.lower().replace("_", "").replace("-", "")


def _extract_metrics(output: Mapping[str, Any]) -> dict[str, float]:
    extracted: dict[str, float] = {}
    for path, value in _flatten(output):
        number = _as_metric_number(value)
        if number is None:
            continue
        if any(term in _leaf_name(path) for term in METRIC_TERMS):
            extracted[path] = number
    return extracted


def _group_metric_key(key: str) -> str:
    return _leaf_name(key)


def _relative_divergence(a: float, b: float) -> float:
    """Symmetric relative difference.

    QA-020: this was ``abs(a - b) / a``, so which value landed in ``a`` — the
    iteration order the orchestrator happened to gather results in — decided
    whether the pair tripped the threshold. (100, 125) is 25% one way and 20%
    the other. The same evaluation run twice could report different counts.
    """
    scale = max(abs(a), abs(b))
    if scale == 0:
        return 0.0
    return abs(a - b) / scale
