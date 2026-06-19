"""Calibration endpoints for inspecting and updating recommendation scorecards."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text as sql_text

from app.database import async_session
from app.knowledge.calibration import get_scorecard, update_checkpoint

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("/scorecard")
async def scorecard():
    return await get_scorecard()


@router.get("/records")
async def list_records(limit: int = 50):
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT id, project_name, ticker, recommendation, overall_score,
                       chair_confidence, entry_price_usd, entry_captured_at,
                       return_30d_pct, return_90d_pct, return_180d_pct,
                       alpha_vs_btc_30d_pct, alpha_vs_btc_90d_pct, alpha_vs_btc_180d_pct,
                       created_at
                FROM calibration_records
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        records = []
        for row in result.fetchall():
            records.append(
                {
                    "id": str(row[0]),
                    "project_name": row[1],
                    "ticker": row[2],
                    "recommendation": row[3],
                    "overall_score": float(row[4]) if row[4] is not None else None,
                    "chair_confidence": row[5],
                    "entry_price_usd": float(row[6]) if row[6] is not None else None,
                    "entry_captured_at": row[7],
                    "return_30d_pct": float(row[8]) if row[8] is not None else None,
                    "return_90d_pct": float(row[9]) if row[9] is not None else None,
                    "return_180d_pct": float(row[10]) if row[10] is not None else None,
                    "alpha_30d": float(row[11]) if row[11] is not None else None,
                    "alpha_90d": float(row[12]) if row[12] is not None else None,
                    "alpha_180d": float(row[13]) if row[13] is not None else None,
                    "created_at": row[14],
                }
            )
        return {"records": records, "count": len(records)}


@router.post("/checkpoint/{record_id}/{horizon_days}")
async def trigger_checkpoint(record_id: str, horizon_days: int):
    result = await update_checkpoint(record_id, horizon_days)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/pending")
async def pending_checkpoints():
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT id, project_name, recommendation, entry_captured_at,
                       EXTRACT(DAY FROM (NOW() - entry_captured_at)) as days_elapsed,
                       price_30d, price_90d, price_180d
                FROM calibration_records
                WHERE entry_captured_at IS NOT NULL
                ORDER BY entry_captured_at ASC
                """
            )
        )
        pending = []
        for row in result.fetchall():
            days_elapsed = int(row[4]) if row[4] is not None else 0
            checkpoints_due: list[int] = []
            if days_elapsed >= 30 and row[5] is None:
                checkpoints_due.append(30)
            if days_elapsed >= 90 and row[6] is None:
                checkpoints_due.append(90)
            if days_elapsed >= 180 and row[7] is None:
                checkpoints_due.append(180)
            if checkpoints_due:
                pending.append(
                    {
                        "id": str(row[0]),
                        "project_name": row[1],
                        "recommendation": row[2],
                        "days_elapsed": days_elapsed,
                        "checkpoints_due": checkpoints_due,
                    }
                )
        return {"pending": pending, "count": len(pending)}
