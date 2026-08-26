"""The thing that makes the cross-report consistency sweep actually happen.

``knowledge/consistency.py`` has held a complete, working sweep and a complete,
working policy — "every 10 reports or every 30 days", in ``audit_is_due`` —
since it was written. In production it had **never executed once**: no crontab
entry, no systemd timer, no orchestrator call, no bot command. The findings
ledger was empty because nothing had ever looked, which is indistinguishable
from a clean corpus until you check ``consistency_audit_runs`` and find zero
rows. This module is the missing caller.

Why in-process rather than a systemd timer
------------------------------------------
The note above ``audit_is_due`` chose "an API endpoint driven by a dumb external
heartbeat" and sketched a ``committee-consistency-audit.timer``. That reasoning
about **where the policy lives** was right and is preserved exactly: the policy
stays in ``audit_is_due``, and the driver below is a dumb heartbeat that only
knows how to ask. What has changed is the judgement about **where the heartbeat
lives**, and three things decide it:

* **The deploy cannot install it.** CONTRACTS §4.7 — the deploy is
  ``git pull --ff-only`` plus (D14) ``docker compose up -d --build backend``. A
  unit file in ``/etc/systemd/system`` is outside that path: it ships by
  somebody remembering to run ``systemctl enable`` by hand, once, on one host.
  The sweep already exists and is already deployed; the only thing missing was
  the caller, and putting the caller somewhere the deploy does not reach
  reproduces the exact failure this module exists to end.
* **It would be invisible to CI and to the repository.** Nothing in the tree
  would show whether the timer is installed, enabled, or firing. "Machinery that
  nothing runs" is not detectable from a checkout when the runner is a host
  artefact. Here, ``GET /api/consistency/schedule`` answers it from the running
  process.
* **A timer's driver is curl.** ``ExecStart=/usr/bin/curl -X POST ...`` reports
  success on any HTTP response it can parse, and its failures land in the
  journal of a unit nobody reads.

The startup-check option the original note rejected — "it fires on deploy
cadence, which is not a cadence" — is still rejected, and this is **not** that.
The loop below runs on wall-clock time for the life of the process; a deploy
starts it, it does not trigger a sweep. See `Startup behaviour` below.

The systemd timer remains perfectly workable as a *second* driver if one is ever
wanted: ``POST /api/consistency/audit`` is unchanged, still idempotent, and the
advisory lock below means an external timer and this loop cannot collide.

Four things this has to get right, each of which has bitten this project
-----------------------------------------------------------------------
1. **It can never take the API down or slow it.** The sweep reads the whole
   corpus and makes external HTTP calls. Every layer is wrapped: the tick body
   catches ``Exception``, the loop catches ``Exception`` around the tick, and
   the lifespan catches ``Exception`` around starting the loop at all. A sweep
   is bounded by ``SWEEP_TIMEOUT_SECONDS`` so a hung socket cannot wedge the
   loop forever. Nothing here ever runs in a request's task, so nothing here
   can add latency to one. A failed audit logs, records, and reschedules.
   ``asyncio.CancelledError`` is deliberately *not* caught — it is a
   ``BaseException`` in 3.12 and shutdown depends on it propagating.

2. **Concurrency.** More than one worker process means more than one loop, and
   two ``run_audit`` calls against one corpus race on the findings ledger. The
   migration runner in ``app/database.py`` already solved exactly this with a
   Postgres advisory lock on a fixed key, so this reuses the pattern — a
   dedicated asyncpg connection, a fixed integer key, released in ``finally``.
   One difference, and it is the point: the migration runner uses blocking
   ``pg_advisory_lock`` because every backend must *have* the schema before it
   serves. A second sweep of an unchanged corpus is worth nothing, so this uses
   **``pg_try_advisory_lock``** and a worker that loses skips quietly at INFO
   rather than queueing behind a fifteen-minute scan.

   Session-scoped, not transaction-scoped, and that is load-bearing for crash
   safety: if the worker dies mid-sweep, Postgres tears down the session and the
   lock goes with it. There is no stale lock to clear by hand, ever.

3. **The policy is asked, never restated.** Nothing in this file knows what 10
   or 30 mean. The tick calls ``audit_is_due()`` and believes the answer. The
   duplicate-policy failure is what D15's branch existed to remove; a scheduler
   with its own copy of the cadence is that failure with a longer fuse, because
   the two copies would only disagree a month after someone edited one.

4. **Observability.** The user has been burned repeatedly by machinery that
   silently did nothing, which is the whole reason this module exists. Every
   tick logs its decision. Every sweep logs its result. ``SchedulerState``
   below is served by ``GET /api/consistency/schedule`` so "is it running, when
   did it last look, what did it decide, what broke" is one curl. And a sweep
   that *fails* now writes a ``status='failed'`` row into
   ``consistency_audit_runs`` with the error text — ``run_audit`` only ever
   writes a row on success, so before this a failed sweep left no trace at all.
   That row is invisible to ``audit_is_due``, which filters on
   ``status = 'completed'``, so a failure correctly does not count as a sweep.

Startup behaviour — the first sweep does NOT fire on boot
---------------------------------------------------------
``audit_is_due`` currently answers ``{"due": true, "reason": "no audit has ever
run"}``, so a sweep on startup would fire immediately and on every restart
after. It is deliberately not wired that way. ``STARTUP_DELAY_SECONDS`` elapses
first, then the normal tick cadence begins.

* ``restart: unless-stopped`` is set on the backend. A container crash-looping
  every thirty seconds would, with a boot-time sweep, run a full-corpus scan and
  a burst of CoinGecko calls on every loop — and CONTRACTS §2.7 measured
  CoinGecko 429ing on the **fourth** call at 8-second spacing. The delay means
  only a container that has stayed up five minutes — a healthy one — ever
  sweeps.
* Deploy cadence is not a cadence: that was the original note's objection to the
  startup-check design and it stands. Firing on boot makes six deploys in a day
  six sweeps.
* It is not a *long* delay, because the sweep must not be starved either. Five
  minutes against a policy measured in tens of days is nothing; the first sweep
  on a never-swept corpus still happens within minutes of the deploy that ships
  this file.
* The belt-and-braces answer to the restart loop is not the delay anyway — it is
  ``audit_is_due`` itself. The first successful sweep writes a completed run
  row, and every restart after that is told "not due" for the next 30 days. The
  policy is what makes repeated boots cheap; the delay just covers the window
  before the first one lands.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text

from app.config import get_settings
from app.database import async_session
from app.knowledge.consistency import audit_is_due, run_audit

logger = logging.getLogger(__name__)

settings = get_settings()


# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------
#
# Module constants rather than `Settings` fields on purpose: CONTRACTS §3.5
# fixes environment-variable names in `app/config.py`, and `config.py` and
# `.env.example` are not this branch's to edit. The recommended `Settings`
# additions — chiefly an operator kill switch that needs no code change — are
# written up in this branch's report for the orchestrator to route.

# Master switch. Flipping this to False leaves the endpoints, the state object
# and every test intact; only the loop stops being started.
SCHEDULER_ENABLED = True

# How long a freshly booted process waits before its first tick. See
# `Startup behaviour` in the module docstring — this is the crash-loop guard,
# not the cadence.
STARTUP_DELAY_SECONDS = 300.0

# How often the loop *asks*. Not the audit cadence: `audit_is_due` owns that and
# will answer "no" to almost every one of these. An hourly question costs one
# indexed lookup plus one COUNT over a corpus of 11 rows. It is set this fine so
# that the "10 new reports" half of the policy responds within an hour of the
# tenth report, rather than waiting out a daily timer.
TICK_INTERVAL_SECONDS = 3600.0

# Hard ceiling on one sweep. The 16-report corpus extracts in well under a
# second; the time is all in `verify_candidate`'s external calls, which carry
# their own 20s httpx timeouts and a CoinGecko backoff ladder on top. This is
# the outer guard against a socket that never returns and never times out.
SWEEP_TIMEOUT_SECONDS = 900.0

# Whether the scheduled sweep checks candidates against ground truth.
#
# True, and it costs nothing from the Anthropic budget. Layer 3 of the module
# docstring in consistency.py describes adjudication as "external / LLM, paid",
# but `verify_candidate` as implemented calls DeFiLlama and CoinGecko over httpx
# and nothing else — there is no LLM provider anywhere in that path. It runs
# with no ANTHROPIC_API_KEY at all, which is why it is on by default even while
# the budget is exhausted.
SWEEP_VERIFY = True

# Adjacent to `app.database._ADVISORY_LOCK_KEY` (81002026) and deliberately
# distinct: the migration runner's lock must not block a sweep, and a sweep must
# not block a starting backend from getting its schema.
ADVISORY_LOCK_KEY = 81002027


# ---------------------------------------------------------------------------
# State — what `GET /api/consistency/schedule` serves
# ---------------------------------------------------------------------------


@dataclass
class SchedulerState:
    """In-process record of what the loop has done. Cheap, and never persisted.

    The durable record is `consistency_audit_runs`; this is the live view that
    answers "is the thing even running", which no table can.
    """

    enabled: bool = SCHEDULER_ENABLED
    started_at: datetime | None = None
    ticks: int = 0
    last_tick_at: datetime | None = None
    next_tick_at: datetime | None = None
    last_decision: str | None = None
    last_due: dict[str, Any] | None = None
    sweeps_run: int = 0
    sweeps_failed: int = 0
    skipped_not_due: int = 0
    skipped_locked: int = 0
    last_sweep_at: datetime | None = None
    last_sweep_summary: dict[str, Any] | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        now = datetime.now(timezone.utc)
        eta = None
        if self.next_tick_at is not None:
            eta = max(0, round((self.next_tick_at - now).total_seconds()))
        return {
            "enabled": self.enabled,
            "running": _task is not None and not _task.done(),
            "started_at": stamp(self.started_at),
            "ticks": self.ticks,
            "last_tick_at": stamp(self.last_tick_at),
            "next_tick_at": stamp(self.next_tick_at),
            "next_tick_in_seconds": eta,
            "last_decision": self.last_decision,
            "last_due": self.last_due,
            "sweeps_run": self.sweeps_run,
            "sweeps_failed": self.sweeps_failed,
            "skipped_not_due": self.skipped_not_due,
            "skipped_locked": self.skipped_locked,
            "last_sweep_at": stamp(self.last_sweep_at),
            "last_sweep_summary": self.last_sweep_summary,
            "last_error": self.last_error,
            "last_error_at": stamp(self.last_error_at),
            "config": {
                "startup_delay_seconds": STARTUP_DELAY_SECONDS,
                "tick_interval_seconds": TICK_INTERVAL_SECONDS,
                "sweep_timeout_seconds": SWEEP_TIMEOUT_SECONDS,
                "verify": SWEEP_VERIFY,
                "advisory_lock_key": ADVISORY_LOCK_KEY,
            },
        }


state = SchedulerState()

_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# The advisory lock
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def audit_lock() -> AsyncIterator[bool]:
    """Hold the sweep lock for the block, or yield ``False`` and hold nothing.

    Mirrors `app/database.py::run_migrations` — asyncpg, a fixed integer key,
    released in `finally` — with `pg_try_advisory_lock` in place of the blocking
    form so a losing worker skips instead of queueing.

    Never raises. A database that cannot be reached yields ``False``: not
    getting the lock and not being able to ask for it have the same correct
    consequence, which is to not sweep. Raising here would put an exception in
    the loop's path for a condition that is not an error.
    """
    conn: Any = None
    acquired = False
    try:
        import asyncpg

        from app.database import _asyncpg_dsn

        # Imported rather than re-derived. The dialect-prefix strip has exactly
        # one correct definition and two copies of it is the drift failure this
        # whole branch is a reaction to.
        conn = await asyncpg.connect(_asyncpg_dsn(settings.database_url))
        acquired = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY))
    except Exception as exc:
        logger.warning("CONSISTENCY SCHEDULE: could not take the audit lock: %s", exc)
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
        yield False
        return

    try:
        yield acquired
    finally:
        # Closing the connection would release a session-level lock on its own;
        # the explicit unlock is here so the intent is legible in a pg_locks
        # dump taken a second before close.
        with contextlib.suppress(Exception):
            if acquired:
                await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_KEY)
        with contextlib.suppress(Exception):
            await conn.close()


# ---------------------------------------------------------------------------
# Failure bookkeeping
# ---------------------------------------------------------------------------


async def _record_failed_run(corpus_size: int, error: str) -> None:
    """Leave a durable trace of a sweep that did not finish.

    `run_audit` writes its `consistency_audit_runs` row only after it completes,
    so a sweep that raised used to leave nothing behind — the same shape of
    silence this module exists to end. `status='failed'` is invisible to
    `audit_is_due`, which selects `status = 'completed'`, so recording a failure
    correctly does not satisfy the policy.

    Best-effort by construction: if the reason the sweep failed was the database,
    this will fail too, and it must not turn one logged error into two.
    """
    try:
        async with async_session() as session:
            await session.execute(
                sql_text(
                    "INSERT INTO consistency_audit_runs "
                    "(started_at, completed_at, status, corpus_size, error) "
                    "VALUES (:started, :completed, 'failed', :corpus, :error)"
                ),
                {
                    "started": datetime.now(timezone.utc),
                    "completed": datetime.now(timezone.utc),
                    "corpus": corpus_size,
                    "error": error[:2000],
                },
            )
            await session.commit()
    except Exception:
        logger.exception("CONSISTENCY SCHEDULE: could not record the failed run")


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------


async def run_tick(*, force: bool = False, verify: bool | None = None) -> dict[str, Any]:
    """Ask whether a sweep is due; if it is and we win the lock, run it.

    Returns a small dict describing what happened, always. **This never raises**
    — every branch is caught and turned into ``{"action": "error"}``. The loop
    depends on that, and so does the operator endpoint that calls it.

    ``force`` bypasses the policy check but *not* the lock: an operator running
    it by hand still must not race a scheduled sweep.
    """
    if verify is None:
        verify = SWEEP_VERIFY

    state.ticks += 1
    state.last_tick_at = datetime.now(timezone.utc)

    # --- ask the policy ----------------------------------------------------
    try:
        due = await audit_is_due()
    except Exception as exc:
        state.last_decision = "error: due check failed"
        state.last_error = f"due check: {exc}"
        state.last_error_at = datetime.now(timezone.utc)
        logger.exception("CONSISTENCY SCHEDULE: due check failed")
        return {"action": "error", "stage": "due", "error": str(exc)}

    state.last_due = dict(due)
    corpus_size = int(due.get("corpus_size") or 0)

    if not due.get("due") and not force:
        state.skipped_not_due += 1
        state.last_decision = f"not due — {due.get('reason')}"
        logger.info("CONSISTENCY SCHEDULE: not due — %s", due.get("reason"))
        return {"action": "skipped", "why": "not_due", **due}

    # --- take the lock -----------------------------------------------------
    async with audit_lock() as got_lock:
        if not got_lock:
            state.skipped_locked += 1
            state.last_decision = "skipped — another worker holds the audit lock"
            logger.info(
                "CONSISTENCY SCHEDULE: due (%s) but another worker holds the audit "
                "lock (key %s) — skipping this tick",
                due.get("reason"),
                ADVISORY_LOCK_KEY,
            )
            return {"action": "skipped", "why": "locked", **due}

        # Re-ask inside the lock. Between this worker's due check and its lock
        # acquisition another worker may have finished a whole sweep, which
        # would make the answer stale. Without this, two schedulers that tick a
        # few seconds apart both sweep — serialised rather than concurrent, so
        # the ledger is safe, but the corpus is scanned twice for nothing.
        if not force:
            try:
                due = await audit_is_due()
            except Exception as exc:
                state.last_decision = "error: due re-check failed"
                state.last_error = f"due re-check: {exc}"
                state.last_error_at = datetime.now(timezone.utc)
                logger.exception("CONSISTENCY SCHEDULE: due re-check failed under lock")
                return {"action": "error", "stage": "due_recheck", "error": str(exc)}
            state.last_due = dict(due)
            corpus_size = int(due.get("corpus_size") or 0)
            if not due.get("due"):
                state.skipped_not_due += 1
                state.last_decision = f"not due on re-check — {due.get('reason')}"
                logger.info(
                    "CONSISTENCY SCHEDULE: another worker swept while we waited — %s",
                    due.get("reason"),
                )
                return {"action": "skipped", "why": "not_due_on_recheck", **due}

        # --- sweep ---------------------------------------------------------
        logger.info(
            "CONSISTENCY SCHEDULE: sweep starting — %s (corpus %d, verify=%s)",
            due.get("reason"),
            corpus_size,
            verify,
        )
        began = datetime.now(timezone.utc)
        try:
            async with asyncio.timeout(SWEEP_TIMEOUT_SECONDS):
                result = await run_audit(verify=verify, persist=True)
        except asyncio.CancelledError:
            # Shutdown, not a failure. Must propagate: `asyncio.timeout` raises
            # TimeoutError rather than CancelledError on expiry, so anything
            # arriving here is a real cancellation of the whole task.
            raise
        except Exception as exc:
            elapsed = (datetime.now(timezone.utc) - began).total_seconds()
            state.sweeps_failed += 1
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.last_error_at = datetime.now(timezone.utc)
            state.last_decision = f"sweep failed after {elapsed:.1f}s"
            logger.exception("CONSISTENCY SCHEDULE: sweep failed after %.1fs", elapsed)
            await _record_failed_run(corpus_size, f"{type(exc).__name__}: {exc}")
            return {"action": "error", "stage": "sweep", "error": str(exc)}

        elapsed = (datetime.now(timezone.utc) - began).total_seconds()
        summary = {
            "audit_run_id": result.audit_run_id,
            "corpus_size": result.corpus_size,
            "claims_extracted": result.claims_extracted,
            "conflicts_found": result.conflicts_found,
            "findings_new": result.findings_new,
            "findings_existing": result.findings_existing,
            "verified": result.verified,
            "elapsed_seconds": round(elapsed, 2),
        }
        state.sweeps_run += 1
        state.last_sweep_at = datetime.now(timezone.utc)
        state.last_sweep_summary = summary
        state.last_decision = (
            f"swept — {result.findings_new} new finding(s) from "
            f"{result.conflicts_found} conflict(s)"
        )
        logger.info(
            "CONSISTENCY SCHEDULE: sweep complete in %.1fs — run %s, corpus %d, "
            "claims %d, conflicts %d, new findings %d, already known %d",
            elapsed,
            result.audit_run_id,
            result.corpus_size,
            result.claims_extracted,
            result.conflicts_found,
            result.findings_new,
            result.findings_existing,
        )
        if result.findings_new:
            # Deliberately WARNING, not INFO. A new cross-report contradiction is
            # the one event here a human should see without going looking, and
            # it is the hook a Telegram notification would attach to (see this
            # branch's report — telegram_bot.py is another branch's file).
            logger.warning(
                "CONSISTENCY SCHEDULE: %d NEW cross-report contradiction(s) recorded "
                "— GET /api/consistency/findings",
                result.findings_new,
            )
        return {"action": "swept", **summary}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def _loop() -> None:
    """Sleep, tick, repeat, forever. Cancelled at shutdown.

    ``run_tick`` already swallows everything, so the ``except Exception`` here is
    the second belt: it exists so that a defect in ``run_tick``'s own error
    handling still cannot end the loop. The failure mode this guards against is
    a scheduler that dies quietly at 03:00 and is never noticed — which is a
    smaller version of the failure this whole module exists to fix.
    """
    state.started_at = datetime.now(timezone.utc)
    logger.info(
        "CONSISTENCY SCHEDULE: started — first tick in %.0fs, then every %.0fs "
        "(no sweep on boot, by design)",
        STARTUP_DELAY_SECONDS,
        TICK_INTERVAL_SECONDS,
    )

    delay = STARTUP_DELAY_SECONDS
    while True:
        state.next_tick_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        await asyncio.sleep(delay)
        delay = TICK_INTERVAL_SECONDS
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            state.last_error_at = datetime.now(timezone.utc)
            logger.exception("CONSISTENCY SCHEDULE: tick raised — rescheduling")


# ---------------------------------------------------------------------------
# Lifecycle — what `main.py`'s lifespan calls
# ---------------------------------------------------------------------------


def start() -> asyncio.Task[None] | None:
    """Start the loop as a background task. Never raises.

    Returns the task, or ``None`` if the scheduler is disabled or could not be
    started. The caller is the FastAPI lifespan, and a scheduler that cannot
    start must not stop the API from serving.
    """
    global _task

    if not SCHEDULER_ENABLED:
        state.enabled = False
        logger.info("CONSISTENCY SCHEDULE: disabled (SCHEDULER_ENABLED=False) — not started")
        return None

    if _task is not None and not _task.done():
        logger.warning("CONSISTENCY SCHEDULE: already running — not starting a second loop")
        return _task

    try:
        _task = asyncio.create_task(_loop(), name="consistency-audit-scheduler")
    except Exception:
        _task = None
        logger.exception("CONSISTENCY SCHEDULE: failed to start — the sweep will not run")
        return None
    return _task


async def stop(timeout: float = 10.0) -> None:
    """Cancel the loop and wait briefly for it to unwind. Never raises."""
    global _task

    task = _task
    _task = None
    if task is None or task.done():
        return

    task.cancel()
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(task, return_exceptions=True)
    except Exception:
        logger.warning("CONSISTENCY SCHEDULE: loop did not stop within %.0fs", timeout)
    logger.info("CONSISTENCY SCHEDULE: stopped")


def status() -> dict[str, Any]:
    """The scheduler's live state, for `GET /api/consistency/schedule`."""
    return state.to_json()


async def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    """The durable half of the answer: rows from `consistency_audit_runs`.

    Read-only. Never raises — the endpoint that serves this must still answer
    "is the loop alive" when the database is the thing that is broken.
    """
    try:
        async with async_session() as session:
            rows = (
                await session.execute(
                    sql_text(
                        "SELECT id, started_at, completed_at, status, corpus_size, "
                        "       claims_extracted, conflicts_found, findings_new, error "
                        "FROM consistency_audit_runs ORDER BY started_at DESC LIMIT :n"
                    ),
                    {"n": limit},
                )
            ).mappings().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {}
            for key, value in dict(row).items():
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
                elif key == "id":
                    item[key] = str(value)
                else:
                    item[key] = value
            out.append(item)
        return out
    except Exception:
        logger.exception("CONSISTENCY SCHEDULE: could not read consistency_audit_runs")
        return []


__all__ = [
    "ADVISORY_LOCK_KEY",
    "SCHEDULER_ENABLED",
    "STARTUP_DELAY_SECONDS",
    "SWEEP_TIMEOUT_SECONDS",
    "SWEEP_VERIFY",
    "TICK_INTERVAL_SECONDS",
    "SchedulerState",
    "audit_lock",
    "recent_runs",
    "run_tick",
    "start",
    "state",
    "status",
    "stop",
]
