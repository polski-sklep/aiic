"""Calibration tracking for evaluating recommendation quality over time."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text as sql_text

from app.database import async_session

logger = logging.getLogger(__name__)
COINGECKO = "https://api.coingecko.com/api/v3"


async def _fetch_price(coingecko_id: str) -> dict[str, Any]:
    if not coingecko_id:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{COINGECKO}/simple/price",
                params={
                    "ids": coingecko_id,
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                },
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Calibration price fetch failed for %s: %s", coingecko_id, exc)
        return {}

    if coingecko_id not in data:
        return {}
    return {
        "price": data[coingecko_id].get("usd"),
        "market_cap": data[coingecko_id].get("usd_market_cap"),
    }


async def _fetch_benchmarks() -> dict[str, float | None]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{COINGECKO}/simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Calibration benchmark fetch failed: %s", exc)
        return {"btc": None, "eth": None}

    return {
        "btc": data.get("bitcoin", {}).get("usd"),
        "eth": data.get("ethereum", {}).get("usd"),
    }


async def record_calibration(
    evaluation_id: str | None,
    project_name: str,
    ticker: str,
    coingecko_id: str,
    category: str,
    recommendation: str,
    overall_score: float | None,
    chair_confidence: str,
    vetoed: bool,
) -> str | None:
    price_data = await _fetch_price(coingecko_id)
    benchmarks = await _fetch_benchmarks()
    now = datetime.now(timezone.utc)

    try:
        async with async_session() as session:
            result = await session.execute(
                sql_text(
                    """
                    INSERT INTO calibration_records (
                        evaluation_id, project_name, ticker, coingecko_id, category,
                        recommendation, overall_score, chair_confidence, vetoed,
                        entry_price_usd, entry_market_cap_usd, entry_captured_at,
                        btc_price_at_entry, eth_price_at_entry
                    ) VALUES (
                        :evaluation_id, :project_name, :ticker, :coingecko_id, :category,
                        :recommendation, :overall_score, :chair_confidence, :vetoed,
                        :entry_price, :entry_mcap, :captured_at,
                        :btc, :eth
                    )
                    RETURNING id
                    """
                ),
                {
                    "evaluation_id": uuid.UUID(evaluation_id) if evaluation_id else None,
                    "project_name": project_name,
                    "ticker": ticker,
                    "coingecko_id": coingecko_id,
                    "category": category,
                    "recommendation": recommendation,
                    "overall_score": overall_score,
                    "chair_confidence": chair_confidence,
                    "vetoed": vetoed,
                    "entry_price": price_data.get("price"),
                    "entry_mcap": price_data.get("market_cap"),
                    "captured_at": now,
                    "btc": benchmarks.get("btc"),
                    "eth": benchmarks.get("eth"),
                },
            )
            record_id = result.scalar()
            await session.commit()
    except Exception as exc:
        logger.warning("Calibration record failed (non-fatal): %s", exc)
        return None

    logger.info(
        "Calibration recorded for %s: recommendation=%s entry_price=%s",
        project_name,
        recommendation,
        price_data.get("price"),
    )
    return str(record_id)


async def update_checkpoint(record_id: str, horizon_days: int) -> dict[str, Any]:
    if horizon_days not in (30, 90, 180):
        return {"error": "horizon must be 30, 90, or 180"}

    column_price = f"price_{horizon_days}d"
    column_checked_at = f"checked_{horizon_days}d_at"
    column_btc = f"btc_price_{horizon_days}d"
    column_return = f"return_{horizon_days}d_pct"
    column_alpha = f"alpha_vs_btc_{horizon_days}d_pct"

    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT coingecko_id, entry_price_usd, btc_price_at_entry
                FROM calibration_records
                WHERE id = :id
                """
            ),
            {"id": uuid.UUID(record_id)},
        )
        row = result.fetchone()
        if not row:
            return {"error": "record not found"}

        coingecko_id, entry_price, btc_entry = row
        price_data = await _fetch_price(coingecko_id)
        benchmarks = await _fetch_benchmarks()
        current_price = price_data.get("price")
        btc_now = benchmarks.get("btc")

        if current_price is None or entry_price is None:
            return {"error": "missing price data"}

        return_pct = ((float(current_price) - float(entry_price)) / float(entry_price)) * 100
        alpha_pct = None
        if btc_now is not None and btc_entry is not None:
            btc_return = ((float(btc_now) - float(btc_entry)) / float(btc_entry)) * 100
            alpha_pct = return_pct - btc_return

        await session.execute(
            sql_text(
                f"""
                UPDATE calibration_records
                SET {column_price} = :price,
                    {column_checked_at} = :checked_at,
                    {column_btc} = :btc_price,
                    {column_return} = :return_pct,
                    {column_alpha} = :alpha_pct
                WHERE id = :id
                """
            ),
            {
                "price": current_price,
                "checked_at": datetime.now(timezone.utc),
                "btc_price": btc_now,
                "return_pct": round(return_pct, 2),
                "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
                "id": uuid.UUID(record_id),
            },
        )
        await session.commit()

    return {
        "record_id": record_id,
        "horizon_days": horizon_days,
        "entry_price": float(entry_price),
        "current_price": float(current_price),
        "return_pct": round(return_pct, 2),
        "alpha_vs_btc_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
    }


async def get_scorecard() -> dict[str, Any]:
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT recommendation, COUNT(*) as n,
                       AVG(return_30d_pct) as avg_30d,
                       AVG(return_90d_pct) as avg_90d,
                       AVG(return_180d_pct) as avg_180d,
                       AVG(alpha_vs_btc_30d_pct) as alpha_30d,
                       AVG(alpha_vs_btc_90d_pct) as alpha_90d,
                       AVG(alpha_vs_btc_180d_pct) as alpha_180d
                FROM calibration_records
                GROUP BY recommendation
                ORDER BY recommendation
                """
            )
        )
        by_recommendation = []
        for row in result.fetchall():
            by_recommendation.append(
                {
                    "recommendation": row[0],
                    "count": row[1],
                    "avg_return_30d": round(float(row[2]), 2) if row[2] is not None else None,
                    "avg_return_90d": round(float(row[3]), 2) if row[3] is not None else None,
                    "avg_return_180d": round(float(row[4]), 2) if row[4] is not None else None,
                    "avg_alpha_30d": round(float(row[5]), 2) if row[5] is not None else None,
                    "avg_alpha_90d": round(float(row[6]), 2) if row[6] is not None else None,
                    "avg_alpha_180d": round(float(row[7]), 2) if row[7] is not None else None,
                }
            )

    buy_90d = next((row["avg_return_90d"] for row in by_recommendation if row["recommendation"] == "BUY"), None)
    pass_90d = next((row["avg_return_90d"] for row in by_recommendation if row["recommendation"] == "PASS"), None)
    discrimination_90d = round(buy_90d - pass_90d, 2) if buy_90d is not None and pass_90d is not None else None

    return {
        "by_recommendation": by_recommendation,
        "discrimination_90d": discrimination_90d,
        "interpretation": (
            "Positive discrimination means BUYs outperformed PASSes. "
            "Flat or negative discrimination means the committee is not separating winners from losers."
        ),
    }
