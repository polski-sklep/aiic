"""An evaluation's recorded status must reflect whether it produced a report.

THE DEFECT, measured against the live database on 26 Aug 2026.

Five of the seventeen persisted evaluations have a `report_writer` row with no
`sections` key. Every one of them is recorded ``status = 'completed'``::

    Polkadot     5e6e4f2d  2026-04-14  completed  Error code: 429 ... quota
    Hyperliquid  b028881a  2026-04-16  completed  Error code: 429 ... quota
    Chainlink    b22be475  2026-06-01  completed  Error code: 429 ... quota
    Aave         0f48a034  2026-06-11  completed  Error code: 429 ... quota
    Hyperliquid  e2d96b62  2026-08-25  completed  keys: summary, raw_output,
                                                        parse_error

Two failure modes, both recorded as success. Four were re-run by hand the same
day, so a report exists — but only because a human was watching.

These tests are hermetic: no socket is opened and no database is touched. The
Report Writer's two failure shapes are produced by running the REAL
``ReportWriter.run()`` against a stubbed router, so what reaches the
orchestrator is what agents/base.py actually builds, not a hand-written dict.
"""
from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tests._support import no_network

from app.agents.base import AgentResult
from app.agents.orchestrator import (
    DATA_AGENT_NAMES,
    _DEGRADATION_ONLY,
    NO_REPORT_STATUSES,
    SCORE_WEIGHTS,
    STATUS_COMPLETED,
    STATUS_GATE_FAILED,
    STATUS_REPORT_FAILED,
    Orchestrator,
    build_run_health,
    report_deliverable_state,
)
from app.agents.report_writer import ReportWriter
from app.llm import LLMResponse

# The exact message the four production rows carry, truncated to its shape.
QUOTA_ERROR = (
    "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
    "please check your plan and billing details.', 'type': 'insufficient_quota'}}"
)

# Hyperliquid e2d96b62's shape: 8,436 output tokens of prose-then-fence that
# stops mid-string inside the object. The report never closes, so `_loads` and
# every balanced-object candidate fail and base.py falls back to
# {summary, raw_output, parse_error}.
TRUNCATED_REPORT = (
    "The prior learnings confirm the committee has repeatedly flagged monthly "
    "insider unlock drips. I'll now compile the full report.\n\n```json\n"
    '{\n  "project_name": "Hyperliquid",\n  "sections": {\n'
    '    "1_executive_summary": "Hyperliquid is a dominant, genuinely '
    "cash-generative per"
)

GOOD_REPORT = json.dumps(
    {
        "project_name": "Hyperliquid",
        "sections": {
            "1_executive_summary": "A real executive summary.",
            "22_overall_score": "58.5",
        },
        "score": 58.5,
        "recommendation": "WATCH",
    }
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubRouter:
    """A router that either raises or returns one canned completion."""

    def __init__(self, *, content: str | None = None, raises: Exception | None = None):
        self._content = content
        self._raises = raises
        self.calls = 0

    async def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return LLMResponse(
            content=self._content or "",
            tool_calls=[],
            model="stub-model",
            tokens_input=100,
            tokens_output=200,
            stop_reason="end_turn",
        )


def run_report_writer(*, content: str | None = None, raises: Exception | None = None) -> AgentResult:
    """Drive the REAL ReportWriter through the REAL BaseAgent.run().

    The point of not hand-building an AgentResult: the two failure shapes this
    module asserts on are the ones agents/base.py produces, including its
    `parse_output` fallback. If base.py changes how it records a failure, these
    tests notice.
    """
    router = _StubRouter(content=content, raises=raises)
    with (
        no_network(),
        mock.patch("app.agents.base.get_llm_router", return_value=router),
        mock.patch("app.agents.base.get_tool_registry", return_value=mock.MagicMock()),
    ):
        return asyncio.run(ReportWriter().run({"project_name": "Hyperliquid"}))


def _ok(name: str, score: float | None = 60.0) -> AgentResult:
    return AgentResult(agent_name=name, output={"summary": f"{name} ok"}, score=score)


def _broken(name: str, message: str = "boom") -> AgentResult:
    return AgentResult(agent_name=name, output={"error": message}, score=None, error=message)


class _Gate:
    passed = True
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    blocking_failures: list[str] = []


class _FailedGate(_Gate):
    passed = False
    blocking_failures = ["mandate exclusion: privacy coin"]


def drive_pipeline(
    report_result: AgentResult,
    *,
    broken_agents: tuple[str, ...] = (),
    chair_output: dict[str, Any] | None = None,
    gate: type[_Gate] = _Gate,
) -> tuple[dict[str, Any], mock.AsyncMock]:
    """Run ``Orchestrator.evaluate`` with every network and database touch stubbed.

    ``_run_agent`` is the single funnel every agent goes through, so replacing
    it substitutes the committee without touching the code under test — the
    status decision, the run-health record and the calibration gate all still
    run for real.
    """
    orchestrator = Orchestrator()
    chair = AgentResult(
        agent_name="committee_chair",
        output=chair_output if chair_output is not None else {"decision": "WATCH", "conviction_level": "medium"},
        score=55.0,
    )

    # Bound as a method by ``patch.object``, so it receives ``self``.
    async def fake_run_agent(_self: Any, agent: Any, ctx: Any, cb: Any = None) -> AgentResult:
        if agent.name == "report_writer":
            return report_result
        if agent.name == "committee_chair":
            return chair
        if agent.name in broken_agents:
            return _broken(agent.name)
        return _ok(agent.name)

    record_calibration = mock.AsyncMock(return_value=None)

    with (
        no_network(),
        mock.patch.object(Orchestrator, "_run_agent", fake_run_agent),
        mock.patch.object(Orchestrator, "_resolve_protocol", mock.AsyncMock(return_value={"project_name": "X"})),
        mock.patch.object(Orchestrator, "_load_prior_evaluation", mock.AsyncMock(return_value=None)),
        mock.patch.object(Orchestrator, "_notion_write", mock.AsyncMock(return_value=None)),
        mock.patch("app.agents.orchestrator.fetch_canonical_defi_facts", mock.AsyncMock(return_value={})),
        mock.patch("app.agents.orchestrator.run_structural_gate", mock.AsyncMock(return_value=gate())),
        mock.patch("app.knowledge.consistency.render_active_warnings", mock.AsyncMock(return_value="")),
        mock.patch("app.knowledge.calibration.record_calibration", record_calibration),
    ):
        result = asyncio.run(orchestrator.evaluate("Hyperliquid", {"ticker": "HYPE"}))
    return result, record_calibration


# ---------------------------------------------------------------------------


class ReportDeliverableStateTest(unittest.TestCase):
    """`sections` is the deliverable. Everything else is not a report."""

    def test_a_quota_exhaustion_is_a_failed_call(self):
        result = run_report_writer(raises=RuntimeError(QUOTA_ERROR))
        # This is the production shape: base.py's except branch.
        self.assertEqual(set(result.output), {"error"})
        self.assertIn("429", result.output["error"])
        usable, reason = report_deliverable_state(result)
        self.assertFalse(usable)
        self.assertEqual(reason, "call_failed")

    def test_an_output_ceiling_truncation_is_unparseable_not_a_report(self):
        """e2d96b62. A `summary` and a `raw_output` look like a degraded success.

        They are not. agents/base.py already settled the identical question one
        stage later — a Chair that hits its ceiling mid-JSON is CHAIR_FAILED and
        stays out of the ledger, because "a parse failure is not a verdict". A
        parse failure is not a report either.
        """
        result = run_report_writer(content=TRUNCATED_REPORT)
        self.assertEqual(set(result.output), {"summary", "raw_output", "parse_error"})
        self.assertIsNone(result.error)  # the call SUCCEEDED; the answer is unusable
        usable, reason = report_deliverable_state(result)
        self.assertFalse(usable)
        self.assertEqual(reason, "unparseable")

    def test_a_well_formed_object_with_no_sections_is_still_not_a_report(self):
        result = run_report_writer(content='{"summary": "all fine", "recommendation": "BUY"}')
        usable, reason = report_deliverable_state(result)
        self.assertFalse(usable)
        self.assertEqual(reason, "no_sections")

    def test_an_empty_sections_object_is_not_a_report(self):
        usable, reason = report_deliverable_state(
            AgentResult(agent_name="report_writer", output={"sections": {}})
        )
        self.assertFalse(usable)

    def test_a_real_report_is_a_report(self):
        result = run_report_writer(content=GOOD_REPORT)
        usable, reason = report_deliverable_state(result)
        self.assertTrue(usable)
        self.assertIsNone(reason)


class QuotaExhaustionIsNotCompletedTest(unittest.TestCase):
    """Failure mode 1: the four 429s."""

    def setUp(self):
        self.report = run_report_writer(raises=RuntimeError(QUOTA_ERROR))
        self.result, self.record_calibration = drive_pipeline(self.report)

    def test_the_run_is_not_recorded_as_completed(self):
        self.assertEqual(self.result["status"], STATUS_REPORT_FAILED)
        self.assertNotEqual(self.result["status"], STATUS_COMPLETED)

    def test_the_reason_is_recorded_not_just_the_fact(self):
        self.assertEqual(self.result["run_health"]["report_failure_reason"], "call_failed")
        self.assertFalse(self.result["run_health"]["report_usable"])

    def test_no_verdict_reaches_the_calibration_ledger(self):
        """Aave 0f48a034 put an INSUFFICIENT_DATA row in the live ledger.

        The Chair reads `draft_report` and nothing else (chair.py
        get_system_prompt never touches `prior_agent_outputs`), so on this path
        its whole view of fifteen agents is a five-key stub whose summary is the
        string "Report incomplete". Whatever it returns is not a committee
        verdict and must not be graded against a future price.
        """
        self.record_calibration.assert_not_awaited()

    def test_the_agent_outputs_are_still_persisted_so_the_run_is_recoverable(self):
        self.assertIn("report_writer", self.result["agent_results"])
        self.assertIn("committee_chair", self.result["agent_results"])


class ParseFailureIsNotADegradedSuccessTest(unittest.TestCase):
    """Failure mode 2: Hyperliquid e2d96b62."""

    def setUp(self):
        self.report = run_report_writer(content=TRUNCATED_REPORT)
        self.result, self.record_calibration = drive_pipeline(self.report)

    def test_the_run_is_not_recorded_as_completed(self):
        self.assertEqual(self.result["status"], STATUS_REPORT_FAILED)

    def test_it_is_distinguished_from_the_quota_case(self):
        """One status, two reasons. The remedies differ: a 429 is transient and
        a re-run fixes it; an output-ceiling truncation will recur on the same
        prompt. Collapsing them would hide that."""
        self.assertEqual(self.result["run_health"]["report_failure_reason"], "unparseable")

    def test_no_verdict_reaches_the_calibration_ledger(self):
        self.record_calibration.assert_not_awaited()


class SuccessStillSucceedsTest(unittest.TestCase):
    """The check that proves the other checks mean something."""

    def setUp(self):
        self.report = run_report_writer(content=GOOD_REPORT)
        self.result, self.record_calibration = drive_pipeline(self.report)

    def test_a_good_run_is_completed(self):
        self.assertEqual(self.result["status"], STATUS_COMPLETED)
        self.assertNotIn(self.result["status"], NO_REPORT_STATUSES)

    def test_a_good_run_still_reaches_the_calibration_ledger(self):
        self.record_calibration.assert_awaited_once()

    def test_a_good_run_reports_a_whole_committee(self):
        health = self.result["run_health"]
        self.assertTrue(health["report_usable"])
        self.assertIsNone(health["report_failure_reason"])
        self.assertEqual(health["failed_agents"], [])
        self.assertEqual(health["score_weight_covered"], 1.0)
        self.assertTrue(health["risk_officer_ran"])
        self.assertTrue(health["chair_decided"])

    def test_the_report_survives_the_pipeline_intact(self):
        self.assertIn("sections", self.result["draft_report"])


class DataAgentFailureIsDegradationNotFailureTest(unittest.TestCase):
    """Plasma d5571fd9: six of eight data agents died, the report was written.

    That run must STAY `completed`. The report is real: api/reports.py should
    list it, knowledge/history.py should offer it as a usable prior, and the
    consistency sweep should extract claims from it. Folding degradation into
    `status` would hide a real report from all three.

    What must change is that it stops looking identical to a clean run.
    """

    def setUp(self):
        self.report = run_report_writer(content=GOOD_REPORT)
        self.broken = (
            "governance_analyst",
            "onchain_analyst",
            "tech_infra_analyst",
            "competitive_intel",
            "field_intel",
            "legal_regulatory",
        )
        self.result, self.record_calibration = drive_pipeline(
            self.report, broken_agents=self.broken
        )

    def test_the_run_is_still_completed(self):
        self.assertEqual(self.result["status"], STATUS_COMPLETED)

    def test_it_still_calibrates(self):
        """A degraded run produced a report and a verdict. It is gradeable, and
        keeping it out of the ledger would bias the scorecard toward clean runs."""
        self.record_calibration.assert_awaited_once()

    def test_the_damage_is_recorded_rather_than_smoothed_away(self):
        health = self.result["run_health"]
        self.assertEqual(health["data_agents_failed"], sorted(self.broken))
        self.assertEqual(sorted(health["degraded_only_failures"]), sorted(self.broken))

    def test_the_renormalised_score_no_longer_passes_for_a_whole_committee(self):
        """`_calc_score` sums the weights that scored and divides by that sum.

        With these six dead, 0.45 of the weight table survives and is
        renormalised to 1.0 — the resulting number is arithmetically
        indistinguishable, in the ledger and in the report, from one computed
        over the whole committee. `score_weight_covered` is the missing fact.
        """
        expected = sum(
            w for n, w in SCORE_WEIGHTS.items() if n not in self.broken
        ) / sum(SCORE_WEIGHTS.values())
        self.assertAlmostEqual(
            self.result["run_health"]["score_weight_covered"], round(expected, 3)
        )
        self.assertLess(self.result["run_health"]["score_weight_covered"], 1.0)
        # And the score itself is unchanged by any of this — D6 holds.
        self.assertIsNotNone(self.result["overall_score"])


class RiskOfficerSilenceIsRecordedTest(unittest.TestCase):
    """Chainlink 75cf1b3d: the Risk Officer exhausted its tool rounds.

    ``vetoed`` is read as ``risk.output.get("veto", False)``, so an agent that
    never answered reads as an agent that cleared the project. Settled decision
    1 (AIIC_HANDOFF §11, PROJECT_DECISIONS D4) is that a veto fires on presence
    of danger and never on absence of evidence. Clearing on absence of evidence
    is the same error with the sign flipped, and it is live.

    Whether that should also be terminal is a governance question about what
    the committee may decide, so this records it and does not decide it.
    """

    def setUp(self):
        self.report = run_report_writer(content=GOOD_REPORT)
        self.result, _ = drive_pipeline(self.report, broken_agents=("risk_officer",))

    def test_the_run_is_still_completed(self):
        self.assertEqual(self.result["status"], STATUS_COMPLETED)

    def test_but_the_record_says_the_risk_officer_never_answered(self):
        self.assertFalse(self.result["run_health"]["risk_officer_ran"])
        self.assertIn("risk_officer", self.result["run_health"]["failed_agents"])

    def test_the_risk_officer_is_not_filed_as_ordinary_degradation(self):
        """It has veto power and 0.15 of the score weight. Listing it beside a
        dead field_intel would be the same laundering in a new place."""
        self.assertNotIn("risk_officer", self.result["run_health"]["degraded_only_failures"])


class ChairFailureIsUnchangedTest(unittest.TestCase):
    """The Chair path already existed and must keep working."""

    def setUp(self):
        self.report = run_report_writer(content=GOOD_REPORT)
        self.result, self.record_calibration = drive_pipeline(
            self.report, chair_output={"parse_error": "hit the ceiling", "raw_output": "{"}
        )

    def test_a_report_was_produced_so_the_run_completed(self):
        """The distinction this whole change exists to make: the artefact was
        produced. The adjudication is what failed, and it has its own record."""
        self.assertEqual(self.result["status"], STATUS_COMPLETED)

    def test_the_decision_is_chair_failed_not_a_verdict(self):
        self.assertEqual(self.result["recommendation"], "CHAIR_FAILED")
        self.assertFalse(self.result["run_health"]["chair_decided"])

    def test_calibration_is_still_skipped(self):
        self.record_calibration.assert_not_awaited()


class GateRejectionIsNotCompletedTest(unittest.TestCase):
    """The orchestrator has always returned `gate_failed`; nothing wrote it down.

    api/evaluate.py hardcoded "completed" on every non-raising path, so a
    project the structural gate REJECTED was recorded as a finished evaluation
    with no report — the same defect one stage earlier.
    """

    def test_a_gated_run_reports_its_own_status(self):
        result, _ = drive_pipeline(
            run_report_writer(content=GOOD_REPORT), gate=_FailedGate
        )
        self.assertEqual(result["status"], STATUS_GATE_FAILED)
        self.assertIn(result["status"], NO_REPORT_STATUSES)
        self.assertFalse(result["run_health"]["report_usable"])


class StatusVocabularyTest(unittest.TestCase):
    """A new enum value existing readers do not understand is its own defect.

    Every consumer of `evaluations.status` tests equality against "completed"
    and none enumerates the failure values, which is exactly what makes a new
    terminal value safe. These assert the two properties that safety rests on.
    """

    def test_every_no_report_status_is_distinct_from_completed(self):
        for status in NO_REPORT_STATUSES:
            self.assertNotEqual(status, STATUS_COMPLETED)

    def test_report_failed_is_not_mistaken_for_a_run_still_in_flight(self):
        """api/reports.py::_load_report_parts answers 202 "still deliberating"
        for {pending, queued, running, in_progress} and 409 "did not complete,
        so no report was produced" for everything else. A terminal status that
        collided with that set would make the report page poll forever."""
        still_running = {"pending", "queued", "running", "in_progress"}
        for status in NO_REPORT_STATUSES:
            self.assertNotIn(status, still_running)

    def test_the_status_column_is_wide_enough(self):
        """`evaluations.status` is TEXT in init.sql, so length is not a
        constraint — this guards the assumption rather than the column."""
        from app.models import Evaluation

        self.assertIsNone(Evaluation.__table__.c.status.type.length)


class RunHealthShapeTest(unittest.TestCase):
    """`run_health` is written to JSONB, so it must be JSON-serialisable and
    must not silently lose a key when an agent set changes."""

    def test_it_round_trips_through_json(self):
        health = build_run_health(
            {"report_writer": _ok("report_writer"), "risk_officer": _ok("risk_officer")},
            report_usable=True,
            report_failure_reason=None,
            decision="WATCH",
            vetoed=False,
        )
        self.assertEqual(json.loads(json.dumps(health)), health)

    def test_the_data_agent_roster_matches_the_pipeline(self):
        """Derived from the classes, so adding a ninth data agent cannot leave
        the health record describing eight."""
        self.assertEqual(
            DATA_AGENT_NAMES,
            frozenset(agent.name for agent in Orchestrator().data_agents),
        )

    def test_an_empty_run_does_not_divide_by_zero(self):
        health = build_run_health({}, report_usable=False, report_failure_reason="call_failed", decision=None, vetoed=False)
        self.assertEqual(health["score_weight_covered"], 0.0)
        self.assertEqual(health["agents_run"], 0)


class BackfillAgreesWithThePipelineTest(unittest.TestCase):
    """The classification rule has two implementations and they must not drift.

    `backend/migrations/manual/backfill_report_failed_status.sql` reconstructs
    `run_health` for the historical rows; `build_run_health` writes it for every
    run from here on. Two copies of one rule is the drift failure D15 exists to
    record, and the first draft of that script had already drifted twice — it
    accepted `"sections": {}` as a report and counted an INSUFFICIENT_DATA chair
    as having decided. Neither shape exists in today's corpus, so both were
    invisible.

    The two were diffed row by row against a production-seeded volume on
    29 Aug 2026 — 19 evaluations x 12 keys, zero mismatches. That check needs a
    database. What is asserted here is the part that can be checked hermetically
    and is what actually drifts: the three agent rosters. Adding a ninth data
    agent or re-weighting the score table changes the Python and leaves the SQL
    behind, and the resulting `score_weight_covered` would be wrong in a way
    nothing would report.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = (
            Path(__file__).resolve().parents[1]
            / "migrations" / "manual" / "backfill_report_failed_status.sql"
        ).read_text(encoding="utf-8")

    def _view_body(self, name: str) -> str:
        start = self.sql.index(f"CREATE TEMP VIEW {name}")
        return self.sql[start:self.sql.index(";", start)]

    def test_the_weight_table_matches_score_weights(self):
        """Compared numerically, not textually: 0.10 in SQL and 0.1 in Python
        are the same weight, and a test that says otherwise fails for a reason
        that is not a defect."""
        body = self._view_body("weights")
        in_sql = {
            name: float(value)
            for name, value in re.findall(r"'([a-z_]+)',\s*([0-9.]+)", body)
        }
        self.assertEqual(in_sql, dict(SCORE_WEIGHTS))

    def test_the_data_agent_roster_matches(self):
        named = set(re.findall(r"'([a-z_]+)'", self._view_body("data_agents")))
        self.assertEqual(named, set(DATA_AGENT_NAMES))

    def test_the_degradation_roster_matches(self):
        body = self._view_body("degradation_only")
        # It is written as `data_agents UNION ALL VALUES (...)`, so the literals
        # in the body are only the four synthesis agents.
        named = set(re.findall(r"'([a-z_]+)'", body)) | set(DATA_AGENT_NAMES)
        self.assertEqual(named, set(_DEGRADATION_ONLY))

    def test_the_backfill_stamps_its_own_provenance(self):
        """A reconstructed record must never pass for one written at run time —
        the same safeguard the calibration backfill uses (D5)."""
        self.assertIn("'backfilled',", self.sql)
        self.assertIn("'backfilled_at',", self.sql)

    def test_the_backfill_still_refuses_to_commit_itself(self):
        """It rewrites production rows. The last word must stay ROLLBACK."""
        self.assertTrue(
            self.sql.rstrip().endswith("ROLLBACK;"),
            "the manual backfill no longer ends in ROLLBACK — it would apply on sight",
        )


if __name__ == "__main__":
    unittest.main()
