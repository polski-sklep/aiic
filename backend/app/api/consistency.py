"""HTTP surface for the cross-report consistency audit.

The "every 10 reports or monthly" *policy* lives in
``app/knowledge/consistency.py::audit_is_due``, in Python, where it is testable
and where there is exactly one copy of it. The driver is a dumb heartbeat that
only knows how to ask. ``GET /api/consistency/due`` exists so the heartbeat can
be cheap, and the sweep is idempotent so an over-eager heartbeat is harmless.

The heartbeat itself is **in-process**: ``app/knowledge/consistency_schedule``
runs a guarded background loop started from the FastAPI lifespan, so it ships
with the code instead of living in a systemd unit that the deploy
(``git pull --ff-only``, CONTRACTS §4.7) cannot install. That module's docstring
argues the choice out. An external timer against ``POST /api/consistency/audit``
still works and is safe to add — the two serialise on a Postgres advisory lock.

``GET /api/consistency/schedule`` reports whether the loop is alive and what it
has done, and ``POST /api/consistency/schedule/tick`` runs one tick by hand.

Nothing in this router writes to ``reports``, ``agent_outputs`` or
``evaluations``. Corrections append a superseding revision to
``consistency_findings``; they never edit a report (CONTRACTS §2.5).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.knowledge.consistency import (
    Status,
    AUDIT_EVERY_N_DAYS,
    AUDIT_EVERY_N_REPORTS,
    RECHECK_INTERVAL_HOURS,
    WARNING_CHAR_BUDGET,
    active_findings,
    audit_is_due,
    recheck_finding,
    render_active_warnings,
    run_audit,
    supersede_finding,
)
from app.knowledge.consistency_schedule import recent_runs as schedule_recent_runs
from app.knowledge.consistency_schedule import run_tick as schedule_run_tick
from app.knowledge.consistency_schedule import status as schedule_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consistency", tags=["consistency"])


class CorrectionRequest(BaseModel):
    """A correction is a new superseding revision, never an edit."""

    correction: str = Field(..., min_length=1, max_length=4000)
    # Typed as the Literal rather than str so an unknown status is rejected at
    # the edge with a 422, instead of reaching supersede_finding as a value its
    # signature does not admit.
    status: Status = Field("confirmed_error")


@router.get("/due")
async def consistency_due():
    """Is a sweep due? Cheap enough for a scheduler to poll.

    The policy — 10 new reports or 30 days — lives in Python rather than in a
    crontab so that changing it is a code change with a test, not an
    undiscoverable edit on a VPS.
    """
    result = await audit_is_due()
    result["policy"] = {
        "every_n_reports": AUDIT_EVERY_N_REPORTS,
        "every_n_days": AUDIT_EVERY_N_DAYS,
    }
    return result


@router.post("/audit")
async def consistency_audit(
    force: bool = Query(False, description="Run even if not due."),
    verify: bool = Query(True, description="Check candidates against CoinGecko/DeFiLlama."),
    persist: bool = Query(True, description="Write findings. False = dry run."),
):
    """Observe, check and flag, across the whole corpus. Safe to run twice.

    Returns without doing anything when a sweep is not due, so a daily timer
    honours a monthly policy without the timer knowing the policy.
    """
    if not force:
        due = await audit_is_due()
        if not due["due"]:
            return {"ran": False, **due}

    try:
        result = await run_audit(verify=verify, persist=persist)
    except Exception:
        # CONTRACTS §3.4: never put the exception string in `detail` for a 500.
        logger.exception("Consistency audit failed")
        raise HTTPException(status_code=500, detail="Consistency audit failed")
    return {"ran": True, **result.to_json()}


@router.get("/schedule")
async def consistency_schedule_status(runs: int = Query(10, ge=0, le=100)):
    """Is the sweep actually being driven, and what has it done?

    The single question this project could not answer about the consistency
    audit for as long as it existed. Two halves, deliberately:

    * ``scheduler`` — live, in-process: is the loop alive, when did it last
      tick, what did it decide, when is the next one, what broke. No table can
      answer "is it running".
    * ``runs`` — durable, from ``consistency_audit_runs``: what actually
      happened, including ``status='failed'`` rows, which the scheduler writes
      and ``run_audit`` does not.

    Both sides are non-raising by construction. A database that is down must
    still let this endpoint say the loop is alive.
    """
    return {
        "scheduler": schedule_status(),
        "runs": await schedule_recent_runs(limit=runs) if runs else [],
    }


@router.post("/schedule/tick")
async def consistency_schedule_tick(
    force: bool = Query(False, description="Bypass the due policy (not the lock)."),
    verify: bool | None = Query(None, description="Override the scheduler's verify default."),
):
    """Run one scheduler tick right now, exactly as the loop would.

    Distinct from ``POST /api/consistency/audit``, which calls ``run_audit``
    directly: this goes through the advisory lock and the failure bookkeeping,
    so it is both the operator's "do it now" lever and the way the scheduled
    path is exercised without waiting out ``TICK_INTERVAL_SECONDS``.

    ``force`` skips the policy check but never the lock — a hand-run sweep still
    must not race a scheduled one.

    ``run_tick`` never raises, so this is always a 200 carrying an ``action`` of
    ``swept`` | ``skipped`` | ``error``. That is not a swallowed failure: a
    failed sweep is reported in the body, logged with its traceback, and written
    to ``consistency_audit_runs`` as ``status='failed'``.
    """
    return await schedule_run_tick(force=force, verify=verify)


@router.get("/findings")
async def consistency_findings(limit: int = Query(50, ge=1, le=200)):
    """Current revision of every unresolved finding, worst first."""
    rows = await active_findings(limit=limit)
    return {"count": len(rows), "findings": rows}


@router.post("/findings/{fingerprint}/recheck")
async def consistency_recheck(fingerprint: str):
    """The second check: re-measure the ground truth and classify.

    Deliberately refuses inside ``RECHECK_INTERVAL_HOURS`` of the first check.
    The interval *is* the measurement — two readings a minute apart would call
    every metric stable and grade every disagreement a confirmed error.
    """
    result = await recheck_finding(fingerprint)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    result["recheck_interval_hours"] = RECHECK_INTERVAL_HOURS
    return result


@router.post("/findings/{fingerprint}/correct")
async def consistency_correct(fingerprint: str, req: CorrectionRequest):
    """Record a correction as a new superseding revision.

    "Correct them if needed" cannot mean rewriting the report. The wrong claim
    stays exactly where it was written; this appends a later revision stating
    what is true and pointing back at what it replaces.
    """
    result = await supersede_finding(fingerprint, correction=req.correction, status=req.status)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/warnings")
async def consistency_warnings(
    char_budget: int = Query(WARNING_CHAR_BUDGET, ge=0, le=8000),
):
    """The rendered block, exactly as an agent would see it.

    Empty string when the corpus is clean, so a caller can splice it in
    unconditionally and pay nothing in the common case. The intended call site
    is ``case_context`` in ``agents/orchestrator.py``, beside
    ``canonical_metrics`` — that file belongs to another branch, so this
    endpoint is the seam.
    """
    text = await render_active_warnings(char_budget=char_budget)
    return {
        "warning": text,
        "chars": len(text),
        # ~4 chars/token for English prose; this is the number paid per agent
        # per run if the block is spliced into the volatile section of the
        # prompt, and it is why the render is hard-capped rather than unbounded.
        "approx_tokens": round(len(text) / 4),
        "char_budget": char_budget,
    }
