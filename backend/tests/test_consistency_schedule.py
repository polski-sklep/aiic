"""The scheduler that finally makes the consistency sweep happen.

Everything asserted here is a property the sweep's *absence* proved matters:
the audit had been deployed for as long as it had existed and had never run
once, because the only thing missing was a caller. So these tests are less
about the happy path than about the four ways a caller can be worse than none —
taking the API down with it, racing another worker, growing a second copy of the
policy, or doing nothing quietly.

Hermetic: no socket, no Postgres, no clock. Every collaborator is patched.
"""
from __future__ import annotations

import asyncio
import contextlib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app.knowledge import consistency_schedule as sched
from app.knowledge.consistency import AuditResult

MODULE = "app.knowledge.consistency_schedule"


def audit_result(**overrides: Any) -> AuditResult:
    base = dict(
        audit_run_id="run-1",
        corpus_size=11,
        claims_extracted=402,
        conflicts_found=2,
        findings_new=2,
        findings_existing=0,
        conflicts=[],
        verified=False,
    )
    base.update(overrides)
    return AuditResult(**base)  # type: ignore[arg-type]


@contextlib.contextmanager
def granted_lock(acquired: bool = True):
    """Replace the Postgres advisory lock with a decision."""

    @contextlib.asynccontextmanager
    async def fake_lock():
        yield acquired

    with mock.patch.object(sched, "audit_lock", fake_lock):
        yield


class FakeResult:
    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return []


class FakeSession:
    """Records every statement executed, so an INSERT can be asserted on."""

    def __init__(self, sink: list[tuple[str, Any]]) -> None:
        self.sink = sink

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        self.sink.append((str(statement), params))
        return FakeResult()

    async def commit(self) -> None:
        return None


def session_factory(sink: list[tuple[str, Any]]):
    def factory() -> FakeSession:
        return FakeSession(sink)

    return factory


class SchedulerTestCase(unittest.IsolatedAsyncioTestCase):
    """Module-level `state` and `_task` are globals; no test may leak into another."""

    def setUp(self) -> None:
        sched.state = sched.SchedulerState()
        sched._task = None

    def tearDown(self) -> None:
        sched.state = sched.SchedulerState()
        sched._task = None


# ---------------------------------------------------------------------------
# 1. The policy is asked, never restated
# ---------------------------------------------------------------------------


class PolicyIsNotDuplicatedTest(SchedulerTestCase):
    """D15's branch existed to delete a second copy of a rule. Not growing one
    back is the single most important property of this module.

    A scheduler carrying its own "10 reports or the 2nd" would agree with
    `audit_is_due` on the day it was written and diverge silently the first time
    either was edited — and the divergence would only surface a month later, as
    a sweep that fired at the wrong time or not at all.
    """

    def test_the_scheduler_source_contains_no_cadence_of_its_own(self):
        source = Path(sched.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        # The policy constants exist and are exported by consistency.py. The
        # scheduler must not import, mirror or re-derive them.
        for name in ("AUDIT_EVERY_N_REPORTS", "AUDIT_DAY_OF_MONTH", "AUDIT_TIMEZONE"):
            self.assertNotIn(
                name, code,
                f"{name} is referenced in the scheduler — the policy has two homes again",
            )

    async def test_a_not_due_answer_is_obeyed_even_though_the_corpus_grew(self):
        """The scheduler has no opinion. `audit_is_due` said no, so: no."""
        due = mock.AsyncMock(return_value={
            "due": False, "reason": "3/10 new reports; last sweep inside the window", "corpus_size": 14,
        })
        run = mock.AsyncMock()
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock():
            out = await sched.run_tick()

        run.assert_not_awaited()
        self.assertEqual(out["action"], "skipped")
        self.assertEqual(out["why"], "not_due")
        self.assertEqual(sched.state.skipped_not_due, 1)
        self.assertEqual(sched.state.sweeps_run, 0)

    async def test_a_due_answer_is_obeyed_whatever_the_reason_says(self):
        due = mock.AsyncMock(return_value={
            "due": True, "reason": "no audit has ever run", "corpus_size": 11,
        })
        run = mock.AsyncMock(return_value=audit_result())
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock():
            out = await sched.run_tick()

        run.assert_awaited_once()
        self.assertEqual(out["action"], "swept")
        self.assertEqual(out["findings_new"], 2)
        self.assertEqual(sched.state.sweeps_run, 1)


# ---------------------------------------------------------------------------
# 2. Concurrency
# ---------------------------------------------------------------------------


class AdvisoryLockTest(SchedulerTestCase):
    """Two workers, one corpus, one append-only ledger."""

    def test_the_lock_key_is_not_the_migration_runners_key(self):
        """Sharing the key would make a sweep block a starting backend's schema.

        `run_migrations` takes a *blocking* `pg_advisory_lock`. If the sweep held
        the same key, a backend booting during a fifteen-minute scan would wait
        it out before serving — turning a background job into a startup stall.
        """
        from app.database import _ADVISORY_LOCK_KEY as migration_key

        self.assertNotEqual(sched.ADVISORY_LOCK_KEY, migration_key)

    async def test_the_worker_that_loses_the_lock_skips_quietly(self):
        """Not an error. A second sweep of one corpus is worth nothing, so the
        loser must return, not queue and not raise."""
        due = mock.AsyncMock(return_value={"due": True, "reason": "x", "corpus_size": 11})
        run = mock.AsyncMock()
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock(acquired=False):
            out = await sched.run_tick()

        run.assert_not_awaited()
        self.assertEqual(out["action"], "skipped")
        self.assertEqual(out["why"], "locked")
        self.assertEqual(sched.state.skipped_locked, 1)
        self.assertIsNone(sched.state.last_error)

    async def test_the_due_check_is_repeated_inside_the_lock(self):
        """The window between "is it due" and "I have the lock" is real.

        Worker B checks (due), waits on nothing in particular, and by the time it
        holds the lock worker A has completed an entire sweep. Without the
        re-check B scans the corpus a second time for nothing — serialised and
        harmless, but pointless work that would look like a scheduler firing
        twice.
        """
        answers = [
            {"due": True, "reason": "no audit has ever run", "corpus_size": 11},
            {"due": False, "reason": "0/10 new reports, 0/30 days", "corpus_size": 11},
        ]
        due = mock.AsyncMock(side_effect=answers)
        run = mock.AsyncMock()
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock():
            out = await sched.run_tick()

        self.assertEqual(due.await_count, 2)
        run.assert_not_awaited()
        self.assertEqual(out["why"], "not_due_on_recheck")

    async def test_force_bypasses_the_policy_but_never_the_lock(self):
        """An operator running it by hand still must not race a scheduled sweep."""
        due = mock.AsyncMock(return_value={"due": False, "reason": "no", "corpus_size": 11})
        run = mock.AsyncMock(return_value=audit_result())

        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock():
            forced = await sched.run_tick(force=True)
        self.assertEqual(forced["action"], "swept")

        run.reset_mock()
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock(acquired=False):
            blocked = await sched.run_tick(force=True)
        run.assert_not_awaited()
        self.assertEqual(blocked["why"], "locked")

    async def test_a_lock_that_cannot_even_be_asked_for_yields_false(self):
        """An unreachable database is not an exception in the loop's path.

        `audit_lock` opens its own asyncpg connection. If that fails there is
        nothing to sweep with anyway, and the correct consequence — do not
        sweep — is the same as losing the lock.
        """
        boom = mock.AsyncMock(side_effect=OSError("connection refused"))
        with mock.patch("asyncpg.connect", boom):
            async with sched.audit_lock() as acquired:
                self.assertFalse(acquired)


# ---------------------------------------------------------------------------
# 3. Nothing escapes into the API
# ---------------------------------------------------------------------------


class FailureIsolationTest(SchedulerTestCase):
    """A background job that can raise into the event loop is worse than no job."""

    async def test_a_raising_sweep_is_caught_reported_and_recorded(self):
        due = mock.AsyncMock(return_value={
            "due": True, "reason": "no audit has ever run", "corpus_size": 11,
        })
        run = mock.AsyncMock(side_effect=RuntimeError("ledger exploded"))
        sink: list[tuple[str, Any]] = []

        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), \
             mock.patch.object(sched, "async_session", session_factory(sink)), \
             granted_lock():
            out = await sched.run_tick()  # must not raise

        self.assertEqual(out["action"], "error")
        self.assertEqual(out["stage"], "sweep")
        self.assertEqual(sched.state.sweeps_failed, 1)
        self.assertIn("ledger exploded", sched.state.last_error or "")

        # The durable half. `run_audit` writes its run row only on success, so
        # before this a failed sweep left no trace anywhere.
        statements = " ".join(text for text, _ in sink)
        self.assertIn("INSERT INTO consistency_audit_runs", statements)
        self.assertIn("'failed'", statements)

    async def test_the_failed_run_row_cannot_satisfy_the_policy(self):
        """`audit_is_due` selects `status = 'completed'`. A failure must not
        count as a sweep, or one broken night would buy 30 days of silence."""
        from app.knowledge import consistency

        source = Path(consistency.__file__).read_text(encoding="utf-8")
        self.assertIn("WHERE status = 'completed'", source)

    async def test_recording_the_failure_failing_does_not_raise_either(self):
        """If the database is why the sweep died, the bookkeeping dies too."""
        def exploding_session() -> Any:
            raise OSError("postgres is gone")

        due = mock.AsyncMock(return_value={"due": True, "reason": "x", "corpus_size": 11})
        run = mock.AsyncMock(side_effect=RuntimeError("boom"))
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), \
             mock.patch.object(sched, "async_session", exploding_session), \
             granted_lock():
            out = await sched.run_tick()

        self.assertEqual(out["action"], "error")

    async def test_a_raising_due_check_is_caught(self):
        due = mock.AsyncMock(side_effect=RuntimeError("count failed"))
        run = mock.AsyncMock()
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run):
            out = await sched.run_tick()

        run.assert_not_awaited()
        self.assertEqual(out["action"], "error")
        self.assertEqual(out["stage"], "due")

    async def test_a_hanging_sweep_is_bounded_by_the_timeout(self):
        """External HTTP with no ceiling would wedge the loop forever."""
        async def never_returns(**kwargs: Any) -> AuditResult:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        due = mock.AsyncMock(return_value={"due": True, "reason": "x", "corpus_size": 11})
        sink: list[tuple[str, Any]] = []
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", never_returns), \
             mock.patch.object(sched, "SWEEP_TIMEOUT_SECONDS", 0.05), \
             mock.patch.object(sched, "async_session", session_factory(sink)), \
             granted_lock():
            out = await sched.run_tick()

        self.assertEqual(out["action"], "error")
        self.assertEqual(out["stage"], "sweep")
        self.assertEqual(sched.state.sweeps_failed, 1)

    async def test_the_loop_survives_a_tick_that_raises(self):
        """`run_tick` swallows everything, so this guards the guard: a defect in
        that error handling must still not end the loop. A scheduler that dies
        silently at 03:00 is the failure this module exists to fix, in
        miniature."""
        calls: list[int] = []

        async def flaky(**kwargs: Any) -> dict[str, Any]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("defect in the tick itself")
            return {"action": "skipped"}

        with mock.patch.object(sched, "run_tick", flaky), \
             mock.patch.object(sched, "STARTUP_DELAY_SECONDS", 0.01), \
             mock.patch.object(sched, "TICK_INTERVAL_SECONDS", 0.01):
            task = asyncio.create_task(sched._loop())
            await asyncio.sleep(0.15)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.assertGreaterEqual(len(calls), 2, "the loop stopped after the first raise")

    async def test_cancellation_is_not_swallowed(self):
        """Shutdown depends on CancelledError propagating out of the loop."""
        with mock.patch.object(sched, "STARTUP_DELAY_SECONDS", 5.0):
            task = asyncio.create_task(sched._loop())
            await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# 4. Startup behaviour
# ---------------------------------------------------------------------------


class StartupBehaviourTest(SchedulerTestCase):
    """`due` is currently true with reason "no audit has ever run". A sweep on
    boot would therefore fire on every restart of a crash-looping container."""

    async def test_starting_the_scheduler_does_not_sweep(self):
        run = mock.AsyncMock()
        due = mock.AsyncMock(return_value={"due": True, "reason": "never run", "corpus_size": 11})
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run):
            task = sched.start()
            self.assertIsNotNone(task)
            await asyncio.sleep(0.05)
            try:
                run.assert_not_awaited()
                due.assert_not_awaited()
                self.assertEqual(sched.state.ticks, 0)
            finally:
                await sched.stop(timeout=2.0)

    async def test_the_first_wait_is_the_startup_delay_not_the_tick_interval(self):
        """One long-ish delay, then the cadence — never the cadence first.

        Recorded rather than waited: the real values are 300s and 3600s and the
        point of the test is that they are used in that order.
        """
        delays: list[float] = []

        class Enough(Exception):
            pass

        async def recording_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
            delays.append(delay)
            if len(delays) >= 3:
                raise Enough

        with mock.patch.object(sched, "run_tick", mock.AsyncMock(return_value={})), \
             mock.patch.object(asyncio, "sleep", recording_sleep):
            with contextlib.suppress(Enough):
                await sched._loop()

        self.assertEqual(delays[0], sched.STARTUP_DELAY_SECONDS)
        self.assertEqual(delays[1], sched.TICK_INTERVAL_SECONDS)
        self.assertEqual(delays[2], sched.TICK_INTERVAL_SECONDS)

    async def test_start_is_idempotent(self):
        """Two lifespans in one process must not mean two loops racing the lock."""
        with mock.patch.object(sched, "STARTUP_DELAY_SECONDS", 5.0):
            first = sched.start()
            second = sched.start()
            try:
                self.assertIs(first, second)
            finally:
                await sched.stop(timeout=2.0)

    async def test_a_scheduler_that_cannot_start_does_not_break_the_lifespan(self):
        """`start()` is called from the FastAPI lifespan. It must never raise."""
        with mock.patch.object(asyncio, "create_task", side_effect=RuntimeError("no loop")), \
             self.assertLogs(MODULE, level="ERROR"):
            self.assertIsNone(sched.start())

    async def test_stop_is_safe_when_nothing_was_started(self):
        await sched.stop(timeout=1.0)  # must not raise


# ---------------------------------------------------------------------------
# 5. Observability
# ---------------------------------------------------------------------------


class ObservabilityTest(SchedulerTestCase):
    """"It ran and found nothing" and "it never ran" must never look alike
    again. That confusion is the entire reason this branch exists."""

    async def test_status_reports_not_running_before_start(self):
        snapshot = sched.status()
        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["ticks"], 0)
        self.assertIsNone(snapshot["last_tick_at"])

    async def test_status_reports_running_and_the_next_tick(self):
        with mock.patch.object(sched, "STARTUP_DELAY_SECONDS", 300.0):
            sched.start()
            try:
                await asyncio.sleep(0.02)
                snapshot = sched.status()
                self.assertTrue(snapshot["running"])
                self.assertIsNotNone(snapshot["next_tick_at"])
                self.assertGreater(snapshot["next_tick_in_seconds"], 0)
            finally:
                await sched.stop(timeout=2.0)

    async def test_a_skip_records_why_it_skipped(self):
        due = mock.AsyncMock(return_value={
            "due": False, "reason": "4/10 new reports, 9/30 days", "corpus_size": 15,
        })
        with mock.patch.object(sched, "audit_is_due", due), granted_lock():
            await sched.run_tick()

        snapshot = sched.status()
        self.assertIn("4/10 new reports", snapshot["last_decision"] or "")
        self.assertEqual(snapshot["last_due"]["corpus_size"], 15)

    async def test_a_sweep_records_what_it_found(self):
        due = mock.AsyncMock(return_value={"due": True, "reason": "never run", "corpus_size": 11})
        run = mock.AsyncMock(return_value=audit_result(conflicts_found=3, findings_new=3))
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock():
            await sched.run_tick()

        summary = sched.status()["last_sweep_summary"]
        self.assertEqual(summary["conflicts_found"], 3)
        self.assertEqual(summary["findings_new"], 3)
        self.assertEqual(summary["audit_run_id"], "run-1")
        self.assertIn("elapsed_seconds", summary)

    async def test_new_findings_are_logged_loudly_enough_to_notice(self):
        """The one event here a human should see without going looking."""
        due = mock.AsyncMock(return_value={"due": True, "reason": "never run", "corpus_size": 11})
        run = mock.AsyncMock(return_value=audit_result(findings_new=2))
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock(), \
             self.assertLogs(MODULE, level="WARNING") as captured:
            await sched.run_tick()

        self.assertTrue(
            any("NEW cross-report contradiction" in line for line in captured.output),
            captured.output,
        )

    async def test_a_clean_sweep_does_not_cry_wolf(self):
        due = mock.AsyncMock(return_value={"due": True, "reason": "never run", "corpus_size": 11})
        run = mock.AsyncMock(return_value=audit_result(conflicts_found=0, findings_new=0))
        with mock.patch.object(sched, "audit_is_due", due), \
             mock.patch.object(sched, "run_audit", run), granted_lock(), \
             self.assertLogs(MODULE, level="INFO") as captured:
            await sched.run_tick()

        self.assertFalse(any(line.startswith("WARNING") for line in captured.output), captured.output)

    async def test_recent_runs_returns_empty_rather_than_raising(self):
        """The endpoint must still be able to say "the loop is alive" when the
        database is the thing that is broken."""
        def exploding_session() -> Any:
            raise OSError("postgres is gone")

        with mock.patch.object(sched, "async_session", exploding_session):
            self.assertEqual(await sched.recent_runs(), [])


# ---------------------------------------------------------------------------
# 6. Wiring
# ---------------------------------------------------------------------------


class WiringTest(unittest.TestCase):
    """The defect being fixed was, precisely, that nothing called the sweep."""

    def test_the_lifespan_starts_and_stops_the_scheduler(self):
        from app import main

        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertIn("consistency_schedule.start()", source)
        self.assertIn("await consistency_schedule.stop()", source)

    def test_the_router_exposes_the_schedule(self):
        from app.main import app

        paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
        self.assertIn("/api/consistency/schedule", paths)
        self.assertIn("/api/consistency/schedule/tick", paths)


# ---------------------------------------------------------------------------
# 7. `SWEEP_VERIFY = True` must stay free
# ---------------------------------------------------------------------------


class VerificationCostsNothingTest(unittest.TestCase):
    """The scheduled sweep verifies by default, and that is only defensible
    while verification makes no model calls.

    `consistency.py`'s own module docstring describes the adjudication layer as
    "external / LLM, paid", which is what the default was set against. As
    implemented, `verify_candidate` reaches DeFiLlama and CoinGecko over httpx
    and nothing else — proven live on 29 Aug 2026 by a container holding no
    `ANTHROPIC_API_KEY` completing a `verified: true` sweep whose only outbound
    host was `api.llama.fi`.

    If that ever stops being true, an hourly loop starts spending money on a
    budget the user has hit spend limits on repeatedly, and it would do it
    quietly. This is the tripwire.
    """

    def test_the_verification_path_reaches_no_llm_provider(self):
        from app.knowledge import consistency

        source = Path(consistency.__file__).read_text(encoding="utf-8")
        start = source.index("async def verify_candidate")
        # verify_candidate plus the two helpers it dispatches to, which is the
        # whole of the path a scheduled sweep takes when SWEEP_VERIFY is on.
        # `classify_recheck` is the next definition after `_verify_coingecko`.
        end = source.index("def classify_recheck", start)
        self.assertGreater(end, start, "the verification path could not be located")
        path_source = source[start:end]

        # Import/call shapes, not bare words: `verify_candidate`'s own docstring
        # says "need no Anthropic key", and a prose match would fail on the
        # sentence asserting the very property under test.
        for banned in (
            "app.llm",
            "get_llm_router",
            "AsyncAnthropic",
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
        ):
            self.assertNotIn(
                banned, path_source,
                f"{banned!r} appears in the verification path — SWEEP_VERIFY=True "
                "is no longer free and the hourly loop is now spending money",
            )

        # And it must still be reaching the two free sources, or the assertion
        # above would pass vacuously on a path that had been gutted.
        self.assertIn("_verify_defillama", path_source)
        self.assertIn("_verify_coingecko", path_source)

    def test_the_scheduler_verifies_by_default(self):
        self.assertTrue(
            sched.SWEEP_VERIFY,
            "verification was turned off — if that was deliberate, update the "
            "operations.md table and the module docstring that justify it",
        )


if __name__ == "__main__":
    unittest.main()
