"""Calibration endpoints for inspecting and updating recommendation scorecards."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text as sql_text

from app.database import async_session
from app.knowledge.calibration import VALID_HORIZONS, get_scorecard, update_checkpoint

logger = logging.getLogger(__name__)

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
async def trigger_checkpoint(
    record_id: str,
    horizon_days: int,
    as_of: date | None = Query(
        default=None,
        description=(
            "Observation date, YYYY-MM-DD. Defaults to entry_captured_at + "
            "horizon_days. The price is always fetched as of this date, never spot."
        ),
    ),
):
    """Record the horizon-N checkpoint for a calibration record.

    The price is fetched as of the target date (default:
    ``entry_captured_at + horizon_days``), never as spot, and
    ``checked_{N}d_at`` is written with that true observation date. A target
    date in the future is rejected and nothing is written.
    """
    if horizon_days not in VALID_HORIZONS:
        raise HTTPException(status_code=400, detail="horizon must be 30, 90, or 180")

    try:
        result = await update_checkpoint(record_id, horizon_days, as_of)
    except ValueError:
        # e.g. record_id is not a UUID
        raise HTTPException(status_code=400, detail="record_id must be a UUID")
    except Exception:
        logger.exception("Checkpoint update failed for record %s", record_id)
        raise HTTPException(status_code=500, detail="Checkpoint update failed")

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/pending")
async def pending_checkpoints():
    """Checkpoints whose target date has passed and which have no price yet.

    Checkpoints are date anchored: horizon N is due once
    ``entry_captured_at + N days`` is in the past. A record that is 67 days old
    with no 30d price is reported as due for its **30-day** mark, with
    ``target_date`` naming the day that must be observed and ``days_overdue``
    saying how late it is. Passing that ``target_date`` back as
    ``?as_of=`` to the checkpoint endpoint reproduces the correct historical
    observation.
    """
    today = datetime.now(timezone.utc).date()
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT id, project_name, recommendation, entry_captured_at,
                       price_30d, price_90d, price_180d
                FROM calibration_records
                WHERE entry_captured_at IS NOT NULL
                  -- A failed committee run has no call to calibrate, and an
                  -- entry price is what every return is computed against.
                  -- Without these two filters such rows report as permanently
                  -- overdue: nothing will ever fill them, so "pending" could
                  -- never reach zero and real checkpoints would sit alongside
                  -- standing false positives.
                  AND recommendation <> 'INSUFFICIENT_DATA'
                  AND entry_price_usd IS NOT NULL
                ORDER BY entry_captured_at ASC
                """
            )
        )
        pending = []
        for row in result.fetchall():
            entry_at = row[3]
            entry_day = entry_at.astimezone(timezone.utc).date() if entry_at.tzinfo else entry_at.date()
            prices = {30: row[4], 90: row[5], 180: row[6]}

            checkpoints_due = []
            for horizon in VALID_HORIZONS:
                target = entry_day + timedelta(days=horizon)
                if target <= today and prices[horizon] is None:
                    checkpoints_due.append(
                        {
                            "horizon_days": horizon,
                            "target_date": target.isoformat(),
                            "days_overdue": (today - target).days,
                        }
                    )

            if checkpoints_due:
                pending.append(
                    {
                        "id": str(row[0]),
                        "project_name": row[1],
                        "recommendation": row[2],
                        "entry_captured_at": entry_at,
                        "days_since_entry": (today - entry_day).days,
                        "checkpoints_due": checkpoints_due,
                    }
                )
        return {"pending": pending, "count": len(pending), "as_of": today.isoformat()}
