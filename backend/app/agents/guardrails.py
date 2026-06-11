"""Guardrails system — structural checks run at Step 2 (GATE).

This is a non-LLM check that runs after Protocol Resolution and before
the full agent pipeline. It catches obvious disqualifiers early to avoid
wasting API credits on projects that fail hard constraints.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from app.utils.types import JSONObject

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    passed: bool
    checks: dict[str, JSONObject]
    blocking_failures: list[str]
    warnings: list[str]


async def run_structural_gate(project_info: JSONObject) -> GateResult:
    """Run structural gate checks.

    These are fast, data-driven checks that don't require LLM calls.
    If any blocking check fails, the evaluation is halted early.

    Args:
        project_info: Dict with keys like coingecko_id, ticker, etc.
            May also contain pre-fetched data from Protocol Resolution.

    Returns:
        GateResult with pass/fail, individual check results, and reasons.
    """
    checks = {}
    blocking = []
    warnings = []

    # Pre-fetched data from resolution step
    price_data = project_info.get("_price_data", {})
    token_data = project_info.get("_token_data", {})

    # === Check 1: Does the project exist on CoinGecko? ===
    coingecko_id = project_info.get("coingecko_id", "")
    if not coingecko_id and not price_data:
        checks["coingecko_listed"] = {"passed": False, "reason": "No CoinGecko ID provided and no price data available"}
        warnings.append("Project not found on CoinGecko — limited data available")
    else:
        checks["coingecko_listed"] = {"passed": True}

    # === Check 2: Market cap minimum ===
    market_cap = price_data.get("market_cap") or token_data.get("market_cap_usd")
    if market_cap and market_cap < 1_000_000:
        checks["min_market_cap"] = {"passed": False, "reason": f"Market cap ${market_cap:,.0f} below $1M minimum"}
        blocking.append(f"Market cap ${market_cap:,.0f} is below $1M minimum threshold")
    elif market_cap:
        checks["min_market_cap"] = {"passed": True, "value": market_cap}
    else:
        checks["min_market_cap"] = {"passed": True, "reason": "No market cap data — skipping check"}
        warnings.append("Market cap data unavailable")

    # === Check 3: FDV/MCap ratio ===
    fdv = token_data.get("fully_diluted_valuation")
    mcap = token_data.get("market_cap_usd") or market_cap
    if fdv and mcap and mcap > 0:
        ratio = fdv / mcap
        if ratio > 50:
            checks["fdv_mcap_ratio"] = {"passed": False, "ratio": round(ratio, 1), "reason": f"FDV/MCap ratio {ratio:.1f}x is extreme (>50x)"}
            blocking.append(f"FDV/MCap ratio {ratio:.1f}x indicates extreme future dilution")
        elif ratio > 10:
            checks["fdv_mcap_ratio"] = {"passed": True, "ratio": round(ratio, 1), "warning": f"FDV/MCap ratio {ratio:.1f}x is high (>10x)"}
            warnings.append(f"FDV/MCap ratio {ratio:.1f}x — significant dilution ahead")
        else:
            checks["fdv_mcap_ratio"] = {"passed": True, "ratio": round(ratio, 1)}
    else:
        checks["fdv_mcap_ratio"] = {"passed": True, "reason": "Insufficient data for FDV check"}

    # === Check 4: Category exclusions ===
    category = (project_info.get("category", "") or "").lower()
    excluded_categories = ["meme", "memecoin", "adult", "gambling"]
    if any(exc in category for exc in excluded_categories):
        checks["category_exclusion"] = {"passed": False, "reason": f"Category '{category}' is excluded by mandate"}
        blocking.append(f"Category '{category}' excluded by investment mandate")
    else:
        checks["category_exclusion"] = {"passed": True}

    # === Check 5: Minimum age (proxy: has genesis_date or sufficient history) ===
    genesis = token_data.get("genesis_date")
    if genesis:
        from datetime import datetime, timezone
        try:
            genesis_dt = datetime.fromisoformat(genesis.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - genesis_dt).days
            if age_days < 90:
                checks["min_age"] = {"passed": False, "age_days": age_days, "reason": f"Project is only {age_days} days old (minimum 90)"}
                blocking.append(f"Project age {age_days} days — below 90-day minimum")
            else:
                checks["min_age"] = {"passed": True, "age_days": age_days}
        except (ValueError, TypeError):
            checks["min_age"] = {"passed": True, "reason": "Could not parse genesis date"}
    else:
        checks["min_age"] = {"passed": True, "reason": "No genesis date available — skipping check"}
        warnings.append("Genesis date unknown — cannot verify minimum age requirement")

    # === Check 6: Volume sanity (if price data available) ===
    volume = price_data.get("volume_24h")
    if volume is not None and volume < 10_000:
        checks["min_volume"] = {"passed": False, "volume": volume, "reason": f"24h volume ${volume:,.0f} is dangerously low"}
        warnings.append(f"24h volume only ${volume:,.0f} — extreme liquidity risk")
    elif volume is not None:
        checks["min_volume"] = {"passed": True, "volume": volume}
    else:
        checks["min_volume"] = {"passed": True, "reason": "No volume data"}

    passed = len(blocking) == 0

    if not passed:
        logger.warning(f"Gate FAILED for {project_info.get('project_name', '?')}: {blocking}")
    else:
        logger.info(f"Gate PASSED for {project_info.get('project_name', '?')} with {len(warnings)} warnings")

    return GateResult(
        passed=passed,
        checks=checks,
        blocking_failures=blocking,
        warnings=warnings,
    )
