"""Data reconciliation - shared case context and numerical consistency checks."""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils.types import JSONObject


def build_case_context(project_name: str, resolved_info: JSONObject) -> JSONObject:
    """Build canonical case context from protocol resolution data."""
    now = datetime.now(timezone.utc)
    price_data = resolved_info.get("_price_data", {})
    token_data = resolved_info.get("_token_data", {})
    return {
        "case_time": now.isoformat(),
        "project_name": project_name,
        "canonical_metrics": {
            "price_usd": price_data.get("price"),
            "market_cap_usd": price_data.get("market_cap") or token_data.get("market_cap_usd"),
            "volume_24h_usd": price_data.get("volume_24h") or token_data.get("total_volume_usd"),
            "fdv_usd": token_data.get("fully_diluted_valuation"),
            "circulating_supply": token_data.get("circulating_supply"),
            "total_supply": token_data.get("total_supply"),
            "max_supply": token_data.get("max_supply"),
        },
        "evaluation_date": now.strftime("%Y-%m-%d"),
        "data_snapshot_note": "Canonical baseline metrics as of evaluation_date. Flag discrepancies with external sources.",
    }


def reconcile_data(agent_outputs: dict[str, JSONObject], case_context: JSONObject) -> JSONObject:
    """Flag numerical inconsistencies across agent outputs."""
    inconsistencies = []
    metrics_by_agent = {
        agent_name: extracted
        for agent_name, output in agent_outputs.items()
        if isinstance(output, dict) and (extracted := _extract_metrics(output))
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
                divergence = _relative_divergence(a["value"], b["value"])
                if divergence > 0.2:
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


def _flatten(d: object, prefix: str = ""):
    items = []
    if isinstance(d, dict):
        for k, v in d.items():
            new_key = ("%s.%s" % (prefix, k)) if prefix else k
            if isinstance(v, dict):
                items.extend(_flatten(v, new_key))
            else:
                items.append((new_key, v))
    return items


def _extract_metrics(output: dict) -> dict[str, float]:
    extracted = {}
    for key, val in _flatten(output):
        if isinstance(val, (int, float)) and val > 0:
            k_lower = key.lower()
            if any(w in k_lower for w in ["tvl", "supply", "revenue", "market_cap", "fdv"]):
                extracted[key] = val
    return extracted


def _group_metric_key(key: str) -> str:
    return key.lower().replace("_", "")


def _relative_divergence(a: float, b: float) -> float:
    if a <= 0:
        return 0.0
    return abs(a - b) / a
