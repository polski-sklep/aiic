"""Degraded runs must say so: app/degradation.py, against the whole corpus.

The defect this file guards is not an exception — it is a run that looks fine.
Plasma d5571fd9 lost six of its seven data agents, renormalised its score over
the surviving 45% of the weight table, and was rendered in a format that a
whole-committee run is rendered in. Every assertion below is either a rule about
that, or a real production run replayed through it.

`CORPUS` is all twenty persisted evaluations that carry a `run_health` record,
read out of production on 2026-08-30. For each one it holds what `agent_outputs`
says (which agents errored, which returned a score, what the Report Writer
produced) and, separately, the `run_health` the orchestrator itself wrote. The
two are compared: `health_from_agent_results` is a second implementation of
`build_run_health`, needed because the API response drops the field, and the
only honest way to run a second implementation is to check it against the first
on every case that exists.

The classification of all twenty is pinned in `EXPECTED_SEVERITY`, so "how often
does this fire, and on what" is a number this suite prints rather than a claim
somebody made once.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import unittest
from dataclasses import dataclass, field

def _load_degradation_by_path():
    """degradation.py with no `app` package on the path — how the bot loads it."""
    import importlib.util

    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "degradation.py"
    spec = importlib.util.spec_from_file_location("probe310_degradation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe310_degradation"] = module  # @dataclass resolves via sys.modules
    spec.loader.exec_module(module)
    return module


# `--probe310` runs this file under the VPS's interpreter (3.10), which has none
# of the backend's dependencies and cannot import `app` at all. In that mode the
# module under test is loaded by path, exactly as telegram_bot.py loads it, and
# only `_probe_310` runs. Everything else needs the real package for the mirror
# assertions against the orchestrator.
if "--probe310" in sys.argv:
    degradation = _load_degradation_by_path()
else:
    from app import degradation


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------
# Only two agent rosters exist across the twenty runs: the fourteen-agent April
# committee (no technical_analyst) and the fifteen-agent one since.
ROSTER_14 = [
    "committee_chair", "competitive_intel", "devils_advocate", "field_intel",
    "governance_analyst", "legal_regulatory", "maturation_scorer", "onchain_analyst",
    "portfolio_manager", "ray_dalio", "report_writer", "risk_officer",
    "tech_infra_analyst", "tokenomics_analyst",
]
ROSTER_15 = sorted(ROSTER_14 + ["technical_analyst"])


@dataclass
class Run:
    """One production evaluation, reduced to the signals health depends on."""

    eid: str
    project: str
    day: str
    roster: int
    status: str
    #: `_calc_score` over the persisted per-agent scores. Verified against
    #: `reports.overall_score` for the six rows that have one — 43.4, 58.5,
    #: 63.3, 27.9, 47.2, 31.6 — all exact. The older rows predate the reports
    #: write side, so this is the only place their score survives.
    score: float | None
    failed: list = field(default_factory=list)
    unscored: list = field(default_factory=list)
    report_writer: str = "sections"
    chair_decision: bool = True
    backfilled: bool = False
    #: `evaluations.run_health` as the orchestrator wrote it.
    stored: dict = field(default_factory=dict)

    def agent_results(self) -> dict:
        """Rebuild the serialised `agent_results` the API would have returned."""
        names = ROSTER_14 if self.roster == 14 else ROSTER_15
        out = {}
        for name in names:
            output: dict = {}
            error = None
            if name in self.failed:
                # Both observed failure signatures are exercised: a transport
                # error on the result, and base.py's parse_output fallback.
                if name == "report_writer" and self.report_writer == "parse":
                    output = {"parse_error": "unterminated string", "raw_output": "{"}
                else:
                    error = "Error code: 429 - exceeded your current quota"
            if name == "report_writer" and self.report_writer == "sections":
                output = {"sections": {"executive_summary": "..."}}
            if name == "committee_chair" and self.chair_decision:
                output = dict(output, decision="WATCH")
            out[name] = {
                "agent_name": name,
                "output": output,
                "score": None if name in self.unscored else 50.0,
                "error": error,
            }
        return out

    def evaluation_payload(self) -> dict:
        """What `POST /api/evaluate` hands the bot, `run_health` absent as today."""
        return {
            "status": self.status,
            "overall_score": self.score,
            "recommendation": "WATCH",
            "agent_results": self.agent_results(),
        }


CORPUS = [
    Run(
        eid='75cf1b3d', project='Chainlink', day='2026-04-10', roster=14, status='completed',
        score=79.2,
        failed=['risk_officer'],
        unscored=['risk_officer'],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 1, 'failed_agents': ['risk_officer'], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 0.85, 'risk_officer_ran': False, 'chair_decided': True},
    ),
    Run(
        eid='c1479a94', project='Aave', day='2026-04-11', roster=14, status='completed',
        score=78.2,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='d5571fd9', project='Plasma', day='2026-04-12', roster=14, status='completed',
        score=26.8,
        failed=['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst'],
        unscored=['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst'],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 6, 'failed_agents': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst'], 'data_agents_total': 7, 'data_agents_failed': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst'], 'score_weight_covered': 0.45, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='5e6e4f2d', project='Polkadot', day='2026-04-14', roster=14, status='report_failed',
        score=None,
        failed=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'],
        unscored=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'],
        report_writer='err', chair_decision=False, backfilled=True,
        stored={'report_usable': False, 'report_failure_reason': 'call_failed', 'agents_run': 14, 'agents_failed': 14, 'failed_agents': ['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'], 'data_agents_total': 7, 'data_agents_failed': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst', 'tokenomics_analyst'], 'score_weight_covered': 0.0, 'risk_officer_ran': False, 'chair_decided': False},
    ),
    Run(
        eid='b70d9d7f', project='Polkadot', day='2026-04-14', roster=14, status='completed',
        score=50.5,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='5a57a961', project='Quai', day='2026-04-15', roster=14, status='completed',
        score=39.1,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='8bcb083b', project='LayerZero', day='2026-04-15', roster=14, status='completed',
        score=49.2,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='07035d61', project='Lombard', day='2026-04-16', roster=14, status='completed',
        score=57.8,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 14, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 7, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='b028881a', project='Hyperliquid', day='2026-04-16', roster=14, status='report_failed',
        score=None,
        failed=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'],
        unscored=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'],
        report_writer='err', chair_decision=False, backfilled=True,
        stored={'report_usable': False, 'report_failure_reason': 'call_failed', 'agents_run': 14, 'agents_failed': 14, 'failed_agents': ['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'tokenomics_analyst'], 'data_agents_total': 7, 'data_agents_failed': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst', 'tokenomics_analyst'], 'score_weight_covered': 0.0, 'risk_officer_ran': False, 'chair_decided': False},
    ),
    Run(
        eid='5b566fc1', project='Chainlink', day='2026-06-01', roster=15, status='completed',
        score=69.0,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='b22be475', project='Chainlink', day='2026-06-01', roster=15, status='report_failed',
        score=None,
        failed=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'],
        unscored=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'],
        report_writer='err', chair_decision=False, backfilled=True,
        stored={'report_usable': False, 'report_failure_reason': 'call_failed', 'agents_run': 15, 'agents_failed': 15, 'failed_agents': ['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'], 'data_agents_total': 8, 'data_agents_failed': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'], 'score_weight_covered': 0.0, 'risk_officer_ran': False, 'chair_decided': False},
    ),
    Run(
        eid='0f48a034', project='Aave', day='2026-06-11', roster=15, status='report_failed',
        score=None,
        failed=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'],
        unscored=['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'],
        report_writer='err', chair_decision=False, backfilled=True,
        stored={'report_usable': False, 'report_failure_reason': 'call_failed', 'agents_run': 15, 'agents_failed': 15, 'failed_agents': ['committee_chair', 'competitive_intel', 'devils_advocate', 'field_intel', 'governance_analyst', 'legal_regulatory', 'maturation_scorer', 'onchain_analyst', 'portfolio_manager', 'ray_dalio', 'report_writer', 'risk_officer', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'], 'data_agents_total': 8, 'data_agents_failed': ['competitive_intel', 'field_intel', 'governance_analyst', 'legal_regulatory', 'onchain_analyst', 'tech_infra_analyst', 'technical_analyst', 'tokenomics_analyst'], 'score_weight_covered': 0.0, 'risk_officer_ran': False, 'chair_decided': False},
    ),
    Run(
        eid='1a94e47d', project='Aave', day='2026-06-11', roster=15, status='completed',
        score=77.2,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='8e4b3c83', project='GMX', day='2026-08-25', roster=15, status='completed',
        score=43.4,
        failed=['committee_chair', 'ray_dalio'],
        unscored=['committee_chair', 'ray_dalio'],
        report_writer='sections', chair_decision=False, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 2, 'failed_agents': ['committee_chair', 'ray_dalio'], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': False},
    ),
    Run(
        eid='be8210d4', project='Hyperliquid', day='2026-08-25', roster=15, status='completed',
        score=58.5,
        failed=['ray_dalio'],
        unscored=['ray_dalio'],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 1, 'failed_agents': ['ray_dalio'], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='e2d96b62', project='Hyperliquid', day='2026-08-25', roster=15, status='report_failed',
        score=63.3,
        failed=['committee_chair', 'ray_dalio', 'report_writer'],
        unscored=['committee_chair', 'ray_dalio', 'report_writer'],
        report_writer='parse', chair_decision=False, backfilled=True,
        stored={'report_usable': False, 'report_failure_reason': 'unparseable', 'agents_run': 15, 'agents_failed': 3, 'failed_agents': ['committee_chair', 'ray_dalio', 'report_writer'], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': False},
    ),
    Run(
        eid='3c5483d5', project='Dolphin', day='2026-08-27', roster=15, status='completed',
        score=27.9,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='40eaf3d8', project='Arbitrum', day='2026-08-27', roster=15, status='completed',
        score=47.2,
        failed=['ray_dalio'],
        unscored=['ray_dalio'],
        report_writer='sections', chair_decision=True, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 1, 'failed_agents': ['ray_dalio'], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
    Run(
        eid='e1b7ac31', project='Kamino', day='2026-08-27', roster=15, status='completed',
        score=54.7,
        failed=['committee_chair', 'ray_dalio'],
        unscored=['committee_chair', 'ray_dalio'],
        report_writer='sections', chair_decision=False, backfilled=True,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 2, 'failed_agents': ['committee_chair', 'ray_dalio'], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': False},
    ),
    Run(
        eid='fb190612', project='Plasma', day='2026-08-29', roster=15, status='completed',
        score=31.6,
        failed=[],
        unscored=[],
        report_writer='sections', chair_decision=True, backfilled=False,
        stored={'report_usable': True, 'report_failure_reason': None, 'agents_run': 15, 'agents_failed': 0, 'failed_agents': [], 'data_agents_total': 8, 'data_agents_failed': [], 'score_weight_covered': 1.0, 'risk_officer_ran': True, 'chair_decided': True},
    ),
]


# The classification of every run in the corpus, pinned. This table IS the
# false-positive answer: nine runs are silent, and every one of the eleven that
# speaks corresponds to a failure the orchestrator itself recorded in
# `run_health` — five with no report at all, two that lost the Chair, one that
# lost the Risk Officer, one that lost 55% of the weight table, two that lost
# Ray Dalio and nothing that scores.
#
# Note what is NOT here: no production run has ever landed in `degraded`, the
# 0.85 < coverage < 1.0 tier. The corpus jumps from 1.0 to 0.85 to 0.45. The
# tier is derived from the band arithmetic rather than fitted to the data, and
# the unit tests below are the only thing exercising it.
EXPECTED_SEVERITY = {
    "75cf1b3d": degradation.SEVERE,      # Chainlink   — Risk Officer never answered
    "c1479a94": degradation.OK,
    "d5571fd9": degradation.SEVERE,      # Plasma      — six data agents, 45% coverage
    "5e6e4f2d": degradation.NO_REPORT,
    "b70d9d7f": degradation.OK,
    "5a57a961": degradation.OK,
    "8bcb083b": degradation.OK,
    "07035d61": degradation.OK,
    "b028881a": degradation.NO_REPORT,
    "5b566fc1": degradation.OK,
    "b22be475": degradation.NO_REPORT,
    "0f48a034": degradation.NO_REPORT,
    "1a94e47d": degradation.OK,
    "8e4b3c83": degradation.SEVERE,      # GMX         — the Chair errored
    "be8210d4": degradation.MINOR,       # Hyperliquid — Ray only, no weight lost
    "e2d96b62": degradation.NO_REPORT,
    "3c5483d5": degradation.OK,
    "40eaf3d8": degradation.MINOR,       # Arbitrum    — Ray only
    "e1b7ac31": degradation.SEVERE,      # Kamino      — the Chair errored
    "fb190612": degradation.OK,
}

BY_ID = {run.eid: run for run in CORPUS}


def health_of(eid: str) -> dict:
    run = BY_ID[eid]
    return dict(run.stored, backfilled=run.backfilled)


# ---------------------------------------------------------------------------
# The copied constants
# ---------------------------------------------------------------------------


class ConstantsMirrorTest(unittest.TestCase):
    """degradation.py may not import the app package, so it copies. Pin the copy.

    A copied weight table that drifts is worse than no copy: the warning would
    describe a committee the pipeline no longer has. These assertions are the
    reason the duplication is allowed to exist.
    """

    def test_score_weights_match_the_orchestrator(self):
        from app.agents.orchestrator import SCORE_WEIGHTS

        self.assertEqual(degradation.SCORE_WEIGHTS, SCORE_WEIGHTS)

    def test_data_agent_names_match_the_orchestrator(self):
        from app.agents.orchestrator import DATA_AGENT_NAMES

        self.assertEqual(degradation.DATA_AGENT_NAMES, DATA_AGENT_NAMES)

    def test_band_thresholds_match_the_orchestrator(self):
        from app.agents.orchestrator import INVEST_SCORE_THRESHOLD, WATCH_SCORE_THRESHOLD

        self.assertEqual(degradation.INVEST_SCORE_THRESHOLD, INVEST_SCORE_THRESHOLD)
        self.assertEqual(degradation.WATCH_SCORE_THRESHOLD, WATCH_SCORE_THRESHOLD)

    def test_agent_failed_agrees_with_the_orchestrator_on_every_signature(self):
        from app.agents.orchestrator import _agent_failed

        class R:
            def __init__(self, output, error=None):
                self.output, self.error = output, error

        cases = [
            ({}, None),
            ({"summary": "fine"}, None),
            ({}, "Error code: 429"),
            ({"parse_error": "unterminated string"}, None),
            ({"error": "tool rounds exhausted"}, None),
            ("not a dict", None),
        ]
        for output, error in cases:
            self.assertEqual(
                degradation.agent_failed({"output": output, "error": error}),
                _agent_failed(R(output, error)),
                "disagreement on output=%r error=%r" % (output, error),
            )


# ---------------------------------------------------------------------------
# Where the severity cut comes from
# ---------------------------------------------------------------------------


class ThresholdDerivationTest(unittest.TestCase):
    def test_severe_cut_is_the_narrowest_band_and_nothing_else(self):
        # WATCH is 60..75, fifteen points wide. Losing 15% of the weight table
        # lets the missing agents move the score by fifteen points, i.e. by a
        # whole band, whatever the score happens to be.
        self.assertEqual(degradation.NARROWEST_BAND, 15.0)
        self.assertEqual(degradation.SEVERE_MAX_COVERAGE, 0.85)

    def test_interval_is_the_full_range_the_arithmetic_permits(self):
        # A score of 40 over 50% coverage: the surviving half contributes
        # 0.5*40 = 20, and the missing half contributes anywhere in 0..50.
        self.assertEqual(degradation.score_interval(40.0, 0.5), (20.0, 70.0))
        # Plasma d5571fd9, the run this module exists for.
        self.assertEqual(degradation.score_interval(26.8, 0.45), (12.1, 67.1))

    def test_a_whole_committee_gets_no_interval(self):
        self.assertIsNone(degradation.score_interval(60.0, 1.0))
        self.assertIsNone(degradation.score_interval(None, 0.5))
        self.assertIsNone(degradation.score_interval(60.0, None))

    def test_bands_spanned(self):
        self.assertEqual(degradation.bands_spanned(12.1, 67.1), ["PASS", "WATCH"])
        self.assertEqual(degradation.bands_spanned(10.0, 20.0), ["PASS"])
        self.assertEqual(degradation.bands_spanned(0.0, 100.0), ["PASS", "WATCH", "INVEST"])
        self.assertEqual(degradation.bands_spanned(67.3, 82.3), ["WATCH", "INVEST"])


# ---------------------------------------------------------------------------
# Two implementations of run health, checked against each other on real runs
# ---------------------------------------------------------------------------


class DerivationAgreementTest(unittest.TestCase):
    """`health_from_agent_results` must reproduce `build_run_health` exactly.

    It exists only because `EvaluateResponse` drops `run_health` on the wire,
    and a second implementation of a rule is a liability unless it is checked.
    This is the check, over every evaluation in production.
    """

    def test_every_production_run_agrees_key_for_key(self):
        for run in CORPUS:
            with self.subTest(run.eid, project=run.project):
                derived = degradation.health_from_agent_results(
                    run.agent_results(), run.status
                )
                for key, expected in run.stored.items():
                    self.assertEqual(
                        derived.get(key), expected,
                        "%s (%s): %s" % (run.eid, run.project, key),
                    )

    def test_the_wire_shaped_payload_reaches_the_same_verdict(self):
        for run in CORPUS:
            with self.subTest(run.eid):
                self.assertEqual(
                    degradation.assess_evaluation(run.evaluation_payload()).severity,
                    EXPECTED_SEVERITY[run.eid],
                )

    def test_a_run_health_on_the_wire_is_preferred_over_recomputing(self):
        # If api/evaluate.py ever adds the field, it wins without a code change.
        payload = BY_ID["fb190612"].evaluation_payload()
        payload["run_health"] = health_of("d5571fd9")
        self.assertEqual(degradation.assess_evaluation(payload).severity, degradation.SEVERE)


# ---------------------------------------------------------------------------
# Classification over the corpus
# ---------------------------------------------------------------------------


class CorpusClassificationTest(unittest.TestCase):
    def test_every_run_lands_where_it_is_pinned(self):
        for run in CORPUS:
            with self.subTest(run.eid, project=run.project, day=run.day):
                self.assertEqual(
                    degradation.assess(health_of(run.eid), run.score).severity,
                    EXPECTED_SEVERITY[run.eid],
                )

    def test_nothing_fires_on_a_run_with_no_recorded_failure(self):
        """The false-positive definition: a warning on an undamaged run."""
        for run in CORPUS:
            whole = (
                run.stored["agents_failed"] == 0
                and run.stored["report_usable"]
                and run.stored["score_weight_covered"] == 1.0
            )
            if whole:
                with self.subTest(run.eid):
                    assessment = degradation.assess(health_of(run.eid), run.score)
                    self.assertEqual(assessment.severity, degradation.OK)
                    self.assertEqual(assessment.block(), "")
                    self.assertEqual(assessment.score_caveat, "")

    def test_nothing_is_silent_on_a_run_with_one(self):
        for run in CORPUS:
            damaged = run.stored["agents_failed"] > 0 or not run.stored["report_usable"]
            if damaged:
                with self.subTest(run.eid):
                    self.assertTrue(degradation.assess(health_of(run.eid), run.score).block())

    def test_the_split_is_the_one_reported(self):
        counts: dict = {}
        for run in CORPUS:
            severity = degradation.assess(health_of(run.eid), run.score).severity
            counts[severity] = counts.get(severity, 0) + 1
        self.assertEqual(
            counts,
            {degradation.OK: 9, degradation.NO_REPORT: 5, degradation.SEVERE: 4,
             degradation.MINOR: 2},
        )


# ---------------------------------------------------------------------------
# What it actually says
# ---------------------------------------------------------------------------


class WordingTest(unittest.TestCase):
    def test_plasma_d5571fd9_names_the_six_and_refuses_the_number(self):
        assessment = degradation.assess(health_of("d5571fd9"), BY_ID["d5571fd9"].score)
        block = assessment.block()
        self.assertEqual(assessment.severity, degradation.SEVERE)
        self.assertIn("DEGRADED RUN — SEVERE", block)
        self.assertIn("6 of 14 agents failed (6 of 7 data agents):", block)
        for agent in ("competitive_intel", "field_intel", "governance_analyst",
                      "legal_regulatory", "onchain_analyst", "tech_infra_analyst"):
            self.assertIn(agent, block)
        self.assertIn("45% of the weight table", block)
        self.assertIn("12.1–67.1", block)
        self.assertIn("PASS/WATCH", block)
        # The caveat rides on the score line, not on a line of its own.
        self.assertEqual(assessment.score_caveat, " [45% of weights — see above]")

    def test_chainlink_75cf1b3d_says_the_veto_seat_was_empty(self):
        assessment = degradation.assess(health_of("75cf1b3d"), BY_ID["75cf1b3d"].score)
        block = assessment.block()
        self.assertEqual(assessment.severity, degradation.SEVERE)
        self.assertIn("The Risk Officer did not answer", block)
        self.assertIn("the question was never asked", block)
        self.assertIn("risk_officer_absent", assessment.reasons)
        # 79.2 over 85% coverage: the true score is 67.3..82.3, which is the
        # difference between WATCH and INVEST on a run reported as INVEST.
        self.assertEqual(assessment.interval, (67.3, 82.3))
        self.assertIn("WATCH/INVEST", block)

    def test_a_chairless_run_says_nobody_adjudicated_the_score(self):
        assessment = degradation.assess(health_of("e1b7ac31"), BY_ID["e1b7ac31"].score)
        self.assertIn("chair_absent", assessment.reasons)
        self.assertIn("nobody adjudicated", assessment.block())
        # Coverage was whole, so the score itself is not caveated.
        self.assertEqual(assessment.score_caveat, "")

    def test_a_minor_run_says_so_without_touching_the_score(self):
        assessment = degradation.assess(health_of("40eaf3d8"), BY_ID["40eaf3d8"].score)
        self.assertEqual(assessment.severity, degradation.MINOR)
        self.assertIn("PARTIAL RUN — MINOR", assessment.block())
        self.assertIn("ray_dalio", assessment.block())
        self.assertIn("No score weight was lost", assessment.block())
        self.assertEqual(assessment.score_caveat, "")

    def test_a_backfilled_record_says_it_was_reconstructed(self):
        self.assertIn("reconstructed", degradation.assess(health_of("d5571fd9"), 26.8).block())
        live = dict(health_of("d5571fd9"))
        live.pop("backfilled")
        self.assertNotIn("reconstructed", degradation.assess(live, 26.8).block())

    def test_severity_is_not_binary(self):
        headlines = {
            degradation.assess(health_of(eid), BY_ID[eid].score).headline
            for eid in ("d5571fd9", "40eaf3d8", "5e6e4f2d")
        }
        self.assertEqual(len(headlines), 3)


# ---------------------------------------------------------------------------
# It must never be the reason a message is lost
# ---------------------------------------------------------------------------


class NeverRaisesTest(unittest.TestCase):
    def test_garbage_yields_silence_not_an_exception(self):
        for junk in (None, "", 0, [], {"score_weight_covered": "seven"},
                     {"failed_agents": "not a list", "agents_failed": "many"},
                     {"report_usable": None}):
            with self.subTest(junk=junk):
                assessment = degradation.assess(junk, "not a score")
                self.assertEqual(assessment.block(), "")
                self.assertEqual(assessment.score_caveat, "")

    def test_assess_evaluation_swallows_everything(self):
        for junk in (None, "x", 3, [], {"agent_results": "not a dict"},
                     {"agent_results": {"a": None}}, {"run_health": 5}):
            with self.subTest(junk=junk):
                self.assertIsInstance(degradation.assess_evaluation(junk), degradation.Assessment)

    def test_an_unknown_record_shape_is_silent_rather_than_alarming(self):
        # A record written by a future pipeline with different keys must not
        # produce a warning nobody can act on.
        self.assertEqual(degradation.assess({"something_else": True}).severity, degradation.OK)


# ---------------------------------------------------------------------------
# The 3.10 constraint — the one that fails no test and kills the feature
# ---------------------------------------------------------------------------


class StandaloneLoadabilityTest(unittest.TestCase):
    """telegram_bot.py loads degradation.py by path under Python 3.10.

    Identical in kind to test_run_cost.StandaloneLoadabilityTest and for the
    identical reason: the bot runs on the VPS system interpreter (3.10.12) and
    `import app.degradation` would execute app/utils/types.py, which needs
    TypeAliasType (3.12+). A violation would fail nothing here and would
    silently drop the warning from every message on the only machine that sends
    them — which is this module's own defect, rebuilt one layer up.
    """

    def source(self) -> str:
        return pathlib.Path(degradation.__file__).read_text(encoding="utf-8")

    def test_no_intra_package_imports(self):
        for line in self.source().splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith(("from app.", "import app.", "from app ")),
                "degradation.py must not import from the app package: %r" % line,
            )

    def test_only_standard_library_is_imported(self):
        allowed = {"dataclasses", "typing", "__future__"}
        for line in self.source().splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                self.assertIn(stripped.split()[1].split(".")[0], allowed, stripped)
            elif stripped.startswith("from ") and " import " in stripped:
                self.assertIn(stripped.split()[1].split(".")[0], allowed, stripped)

    def test_loads_from_a_bare_spec_with_no_package_context(self):
        import importlib.util

        name = "degradation_standalone_probe"
        spec = importlib.util.spec_from_file_location(name, degradation.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # required: @dataclass resolves via sys.modules
        try:
            spec.loader.exec_module(module)
            self.assertEqual(module.SCORE_WEIGHTS, degradation.SCORE_WEIGHTS)
            self.assertIn(
                "DEGRADED RUN — SEVERE",
                module.assess(health_of("d5571fd9"), 26.8).block(),
            )
        finally:
            sys.modules.pop(name, None)

    def test_it_runs_under_a_real_310_interpreter(self):
        """Skipped where 3.10 is absent; run for real in CI and by `make`.

        The container is 3.12, so this is a best-effort in-suite check. The
        blocking version of it is `docker run --rm -v "$PWD":/src python:3.10-slim
        python3 /src/backend/tests/test_degraded_warning.py --probe310`, which
        is what the branch report pastes.
        """
        interpreter = shutil.which("python3.10")
        if not interpreter:
            self.skipTest("no python3.10 on this machine; see the docstring")
        result = subprocess.run(
            [interpreter, str(pathlib.Path(__file__).resolve()), "--probe310"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DEGRADED RUN", result.stdout)


def _probe_310() -> int:
    """Render the Plasma d5571fd9 warning under whatever interpreter runs this.

    Entry point for `python3.10 backend/tests/test_degraded_warning.py
    --probe310`. Touches nothing from `app`, so it runs on an interpreter with
    none of the backend's dependencies — which is the machine that sends the
    messages.
    """
    print("python %s" % sys.version.split()[0])
    print(degradation.assess(health_of("d5571fd9"), BY_ID["d5571fd9"].score).block())
    return 0


if __name__ == "__main__":
    if "--probe310" in sys.argv:
        raise SystemExit(_probe_310())
    unittest.main()
