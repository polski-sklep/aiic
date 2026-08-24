"""Guardrails system — structural checks run at Step 2 (GATE).

This is a non-LLM check that runs after Protocol Resolution and before
the full agent pipeline. It catches obvious disqualifiers early to avoid
wasting API credits on projects that fail hard constraints.
"""
from __future__ import annotations
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

EXCLUDED_CATEGORIES = ("meme", "adult", "gambling")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """A block of pre-fetched data, or an empty one.

    QA-015: ``.get(key, {})`` only defends against an *absent* key. Protocol
    Resolution setting ``_price_data`` to None on a failed lookup — the obvious
    way to say "we tried and got nothing" — took the gate down with an
    AttributeError before any check ran. The gate is the step whose whole job is
    to fail gracefully on thin data.
    """
    return value if isinstance(value, Mapping) else {}


def _as_number(value: Any) -> float | None:
    """A finite number, or None. Never raises.

    QA-017: the gate compared whatever arrived against 1_000_000 with no
    coercion, so a string-typed figure crashed it.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _category_terms(project_info: Mapping[str, Any], token_data: Mapping[str, Any]) -> list[str]:
    """Every category label we have, from either source.

    QA-018: the exclusion read only the caller-supplied free-text ``category``.
    ``_token_data`` carries CoinGecko's real ``categories`` list, which is where
    the truth about a memecoin lives, and the API accepts ``category`` as
    optional — so any caller that did not hand-populate it bypassed the mandate
    exclusion entirely.

    QA-017: ``(x or "").lower()`` guards None, but a non-empty list is truthy and
    has no ``.lower()``, so passing CoinGecko's own list straight in killed the
    gate.
    """
    terms: list[str] = []
    for raw in (project_info.get("category"), token_data.get("categories")):
        if isinstance(raw, str):
            terms.append(raw)
        elif isinstance(raw, (list, tuple, set)):
            terms.extend(str(item) for item in raw if item is not None)
    return [term.strip().lower() for term in terms if term and term.strip()]


def _genesis_age_days(genesis: Any) -> int | None:
    """Age in days from a CoinGecko genesis_date, or None if unusable.

    QA-014: CoinGecko returns a bare ``"YYYY-MM-DD"``. ``fromisoformat`` makes
    that a *naive* datetime, subtracting it from an aware ``now(timezone.utc)``
    raised TypeError, the except clause swallowed it, and the check recorded
    itself as passed. Only a genesis_date that already carried a timezone ever
    blocked, and ``get_token_info`` passes CoinGecko's value through untouched —
    so the 90-day minimum was dead against every real input.

    A bare calendar date is read as midnight UTC, which is the only reading that
    does not silently discard the check. QA-017: a non-string genesis (a unix
    timestamp) raised AttributeError, which the old except clause did not catch.
    """
    if not isinstance(genesis, str) or not genesis.strip():
        return None
    try:
        parsed = datetime.fromisoformat(genesis.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).days


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

    # Pre-fetched data from resolution step. Every one of these may be absent,
    # explicitly None, or the wrong type; none of that may take the gate down.
    project_info = _as_mapping(project_info)
    price_data = _as_mapping(project_info.get("_price_data"))
    token_data = _as_mapping(project_info.get("_token_data"))

    # === Check 1: Does the project exist on CoinGecko? ===
    coingecko_id = project_info.get("coingecko_id", "")
    if not coingecko_id and not price_data:
        checks["coingecko_listed"] = {"passed": False, "reason": "No CoinGecko ID provided and no price data available"}
        warnings.append("Project not found on CoinGecko — limited data available")
    else:
        checks["coingecko_listed"] = {"passed": True}

    # === Check 2: Market cap minimum ===
    # QA-016: this was `if market_cap and market_cap < 1_000_000`, so a market
    # cap of exactly 0 — delisted, pre-launch, or a CoinGecko gap reported as 0
    # rather than null — fell through to "No market cap data" and passed. Zero
    # is not missing data; it is the most extreme possible failure of the
    # minimum. Absence is now tested for explicitly instead of by truthiness.
    market_cap = _as_number(price_data.get("market_cap"))
    if market_cap is None:
        market_cap = _as_number(token_data.get("market_cap_usd"))
    if market_cap is not None and market_cap < 1_000_000:
        checks["min_market_cap"] = {"passed": False, "reason": f"Market cap ${market_cap:,.0f} below $1M minimum"}
        blocking.append(f"Market cap ${market_cap:,.0f} is below $1M minimum threshold")
    elif market_cap is not None:
        checks["min_market_cap"] = {"passed": True, "value": market_cap}
    else:
        checks["min_market_cap"] = {"passed": True, "reason": "No market cap data — skipping check"}
        warnings.append("Market cap data unavailable")

    # === Check 3: FDV/MCap ratio ===
    fdv = _as_number(token_data.get("fully_diluted_valuation"))
    mcap = _as_number(token_data.get("market_cap_usd"))
    if mcap is None:
        mcap = market_cap
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
    excluded_hits = [
        term
        for term in _category_terms(project_info, token_data)
        if any(excluded in term for excluded in EXCLUDED_CATEGORIES)
    ]
    if excluded_hits:
        listed = ", ".join(f"'{term}'" for term in excluded_hits)
        checks["category_exclusion"] = {"passed": False, "reason": f"Category {listed} is excluded by mandate"}
        blocking.append(f"Category {listed} excluded by investment mandate")
    else:
        checks["category_exclusion"] = {"passed": True}

    # === Check 5: Minimum age (proxy: has genesis_date or sufficient history) ===
    genesis = token_data.get("genesis_date")
    age_days = _genesis_age_days(genesis)
    if age_days is not None:
        if age_days < 90:
            checks["min_age"] = {"passed": False, "age_days": age_days, "reason": f"Project is only {age_days} days old (minimum 90)"}
            blocking.append(f"Project age {age_days} days — below 90-day minimum")
        else:
            checks["min_age"] = {"passed": True, "age_days": age_days}
    elif genesis:
        checks["min_age"] = {"passed": True, "reason": "Could not parse genesis date"}
        warnings.append("Genesis date unreadable — cannot verify minimum age requirement")
    else:
        checks["min_age"] = {"passed": True, "reason": "No genesis date available — skipping check"}
        warnings.append("Genesis date unknown — cannot verify minimum age requirement")

    # === Check 6: Volume sanity (if price data available) ===
    volume = _as_number(price_data.get("volume_24h"))
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
