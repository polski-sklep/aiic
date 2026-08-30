"""Cross-report consistency sweep — the defects it must never reintroduce.

Every fixture below is either a live crash repro or verbatim text from the
sixteen persisted evaluations. Nothing here is invented prose.
"""
from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import app.knowledge.consistency as consistency_module

from app.knowledge.consistency import (
    AUDIT_DAY_OF_MONTH,
    AUDIT_EVERY_N_REPORTS,
    AUDIT_TIMEZONE,
    Claim,
    detect_conflicts,
    extract_claims,
    fingerprint_of,
    sweep_window_start,
)

AUG = datetime(2026, 8, 25, tzinfo=timezone.utc)


def claim(
    entity="Hyperliquid",
    metric="perp_market_share_pct",
    value=44.0,
    period="2026-01",
    evaluation_id="eval-a",
    *,
    unit="pct",
    explicit=True,
    section="7_competitive_landscape",
    report_date="2026-08-01",
):
    return Claim(
        entity=entity, metric=metric, unit=unit, lo=value, hi=value, hedged=False,
        period=period, evaluation_id=evaluation_id, report_project=entity,
        section=section, quote=f"{entity} holds {value}% share in {period}.",
        raw=f"{value}%", period_explicit=explicit, report_date=report_date,
    )


class DateAttributionPeriodTest(unittest.TestCase):
    """`periods` leaked out of the value-conflict loop into this one.

    Bound only inside `for clashing in clusters:` and read inside
    `for same_value in seen.values():`. With no value conflict anywhere in the
    corpus it was never bound at all; with one, it held an unrelated bucket's
    period. Because `period` feeds `fingerprint_of`, the leak also meant one
    logical finding could carry two identities depending on what else the
    corpus happened to contain.
    """

    #: The reported repro, verbatim.
    DATE_ONLY = [
        claim(period="2026-01", evaluation_id="eval-a"),
        claim(period="2026-06", evaluation_id="eval-b"),
    ]

    #: A value conflict on an unrelated bucket, which used to bind `periods`.
    VALUE_CONFLICT_FIRST = [
        claim(entity="Aave", value=10.0, period="2026-03", evaluation_id="eval-x"),
        claim(entity="Aave", value=90.0, period="2026-03", evaluation_id="eval-y"),
    ]

    def test_a_date_attribution_only_corpus_does_not_crash(self):
        conflicts = detect_conflicts(self.DATE_ONLY)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].date_attribution)

    def test_the_period_is_built_from_the_findings_own_claims(self):
        found = detect_conflicts(self.DATE_ONLY)[0]
        self.assertEqual(found.period, "2026-01 vs 2026-06")
        self.assertIn("2026-01 and 2026-06", found.note)

    def test_no_period_appears_that_no_claim_asserts(self):
        conflicts = detect_conflicts(self.VALUE_CONFLICT_FIRST + self.DATE_ONLY)
        date_finding = next(c for c in conflicts if c.date_attribution)
        stated = {c.period for c in date_finding.claims}
        for part in date_finding.period.split(" vs "):
            self.assertIn(part, stated, f"{part!r} is asserted by no claim in the finding")

    def test_the_fingerprint_does_not_depend_on_the_rest_of_the_corpus(self):
        """The identity of a finding is its own claims, not its neighbours."""
        alone = detect_conflicts(self.DATE_ONLY)[0]
        crowded = next(
            c for c in detect_conflicts(self.VALUE_CONFLICT_FIRST + self.DATE_ONLY)
            if c.date_attribution
        )
        self.assertEqual(fingerprint_of(alone), fingerprint_of(crowded))

    def test_the_hyperliquid_44_percent_case_is_still_reported(self):
        """The shape the module exists to catch must survive the fix."""
        found = detect_conflicts(self.DATE_ONLY)[0]
        self.assertEqual(found.entity, "Hyperliquid")
        self.assertEqual(found.metric, "perp_market_share_pct")
        self.assertEqual(found.severity, "high")
        self.assertEqual(found.spread_pct, 0.0)

    def test_a_value_conflict_still_reports_its_own_period(self):
        value_finding = next(
            c for c in detect_conflicts(self.VALUE_CONFLICT_FIRST) if not c.date_attribution
        )
        self.assertEqual(value_finding.period, "2026-03")


class SweepBindingTest(unittest.TestCase):
    """The extractor must consult the noun governing a figure, not only the
    metric phrase nearest to it. Fixtures are verbatim corpus text."""

    def _metrics(self, text, entity="GMX", project="GMX"):
        return {
            (c.entity, c.metric, c.raw)
            for c in extract_claims(
                text, evaluation_id="e1", report_project=project,
                section="5_on_chain_metrics", report_date=AUG,
            )
        }

    def test_a_buyback_is_not_a_thirty_day_volume(self):
        """GMX 8e4b3c83, section 5_on_chain_metrics, verbatim.

        The mis-binding that was read as an 840x contradiction and cost a whole
        module. "over 30 days" is thirteen characters away with no digit in the
        gap, so adjacency alone binds it; "Buybacks:" is never consulted.
        """
        found = self._metrics("Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days;")
        self.assertNotIn(("GMX", "volume_30d_usd", "$3,341,200"), found)
        self.assertEqual(found, set())

    def test_the_real_thirty_day_volume_beside_it_still_binds(self):
        """Dropping the buyback must not cost the volume claim in the same run."""
        found = self._metrics(
            "Hyperliquid commands 70-80%+ of on-chain perp volume with ~$6.66B TVL "
            "and processes ~$245B over 30 days, versus GMX's ~$2.8B 30-day volume."
        )
        self.assertIn(("GMX", "volume_30d_usd", "~$2.8B"), found)
        self.assertIn(("Hyperliquid", "volume_30d_usd", "~$245B"), found)

    def test_a_percentage_of_a_metric_is_a_denominator(self):
        """"~2.7% of market cap (~$1.16B)" — the false positive this module's own
        docstring records, in the one shape its no-digit rule cannot see."""
        found = self._metrics(
            "NEXT UNLOCK: ~14.175M HYPE tokens unlock August 29, 2026, representing "
            "1.4% of total supply and ~2.7% of market cap (~$1.16B).",
            project="Hyperliquid",
        )
        self.assertNotIn(("Hyperliquid", "market_cap_usd", "~$1.16B"), found)

    def test_ordinary_label_then_bracket_phrasing_still_binds(self):
        """The corpus's normal way of stating a figure must survive the rules."""
        self.assertIn(
            ("GMX", "market_cap_usd", "$75M"),
            self._metrics("GMX is trading at $7.19, a $75M market cap, 92% below its ATH."),
        )
        self.assertIn(
            ("GMX", "fdv_usd", "~$75M"),
            self._metrics("GMX is a revenue-generating protocol with a reasonable FDV "
                          "(~$75M, 79% circulating, minimal dilution)."),
        )

    def test_the_measured_recall_cost_of_the_bracket_rule(self):
        """A cost this branch measured and accepted, pinned so it stays visible.

        Hyperliquid be8210d4, section 7, verbatim. "$15B+ daily at peak" really
        is Aster's 24-hour volume, and rule 1 removes it because "~20.9%" sits
        in front of it inside the same bracket. Two things make the loss
        tolerable: the figure is peak-qualified, which the sweep has no way to
        tell apart from a current one and would eventually read as drift; and
        Aster is named once in eleven reports, so the claim is in no finding.
        If a later change makes this claim survive, that is an improvement —
        but it must be a deliberate one, not a silent one.
        """
        found = self._metrics(
            "Named rivals with numbers: Aster (~20.9% share, $15B+ daily at peak);",
            project="Hyperliquid",
        )
        self.assertNotIn(("Aster", "volume_24h_usd", "$15B+"), found)

    def test_a_label_reaching_backwards_over_a_comma_still_binds_here(self):
        """The cross-report sweep deliberately does NOT take reconciliation's
        backward-clause-break rule. "On market cap, Hyperliquid is ~$18.3B" is
        the exact adjacency `_METRIC_WINDOW`'s docstring cites as legitimate,
        and it is a true claim in the live GMX report."""
        self.assertIn(
            ("Hyperliquid", "market_cap_usd", "~$18.3B"),
            self._metrics("On market cap, Hyperliquid is ~$18.3B vs GMX $75M.",
                          project="GMX"),
        )


class SweepSafetyTest(unittest.TestCase):
    def test_an_empty_corpus_yields_no_conflicts_and_no_crash(self):
        self.assertEqual(detect_conflicts([]), [])

    def test_one_evaluation_alone_can_never_conflict_with_itself(self):
        same = [claim(period="2026-01"), claim(period="2026-06")]
        self.assertEqual(detect_conflicts(same), [])


# ---------------------------------------------------------------------------
# The calendar trigger
# ---------------------------------------------------------------------------
#
# `audit_is_due` is "10 new reports OR the calendar arm". The report arm is a
# plain subtraction and is exercised through the scheduler tests; everything
# subtle lives in the calendar arm, and all of it reduces to one boundary:
# `sweep_window_start`. These tests pin the boundary directly, and then pin the
# *decision* — "has a completed sweep started since it?" — as the caller applies
# it, because it is the pair that is load-bearing, not either half alone.


WARSAW = ZoneInfo(AUDIT_TIMEZONE)


def _warsaw(y, m, d, hh=0, mm=0):
    """A local Warsaw wall-clock instant, as UTC — how the driver would see it."""
    return datetime(y, m, d, hh, mm, tzinfo=WARSAW).astimezone(timezone.utc)


def _due_on(now, last_started_at, *, new_reports=0):
    """Replay `audit_is_due`'s calendar arm without a database.

    Deliberately mirrors the two branches of the real function rather than
    mocking a session: the thing under test is the comparison, and a test that
    stubs the comparison tests nothing. If the real function's shape changes,
    `test_the_helper_matches_the_shipped_decision_logic` fails.
    """
    if new_reports >= AUDIT_EVERY_N_REPORTS:
        return True
    return last_started_at < sweep_window_start(now)


class SweepWindowTest(unittest.TestCase):
    """Where the month's window opens."""

    def test_the_window_opens_at_local_midnight_on_the_configured_day(self):
        start = sweep_window_start(_warsaw(2026, 9, 15, 13, 0))
        self.assertEqual(start, _warsaw(2026, 9, AUDIT_DAY_OF_MONTH))
        self.assertEqual(start.astimezone(WARSAW).day, AUDIT_DAY_OF_MONTH)
        self.assertEqual(start.astimezone(WARSAW).hour, 0)

    def test_before_the_day_arrives_the_open_window_is_last_months(self):
        """On the 1st, the 2nd has not happened yet. The window still open is
        the one that opened last month — so a sweep that ran then still counts
        and the trigger stays quiet."""
        self.assertEqual(
            sweep_window_start(_warsaw(2026, 9, 1, 23, 59)),
            _warsaw(2026, 8, AUDIT_DAY_OF_MONTH),
        )

    def test_january_rolls_back_into_the_previous_year(self):
        self.assertEqual(
            sweep_window_start(_warsaw(2026, 1, 1, 12, 0)),
            _warsaw(2025, 12, AUDIT_DAY_OF_MONTH),
        )

    def test_the_boundary_is_read_in_warsaw_not_utc(self):
        """00:30 Warsaw on the 2nd is 22:30 UTC on the 1st. Warsaw semantics say
        the window is open; UTC semantics would say it is not. This test is the
        record of which one was chosen — see AUDIT_TIMEZONE."""
        just_after_midnight_local = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, 0, 30)
        self.assertEqual(just_after_midnight_local.astimezone(timezone.utc).day,
                         AUDIT_DAY_OF_MONTH - 1)
        self.assertLessEqual(sweep_window_start(just_after_midnight_local),
                             just_after_midnight_local)

    def test_the_window_is_stable_for_the_whole_month(self):
        """Every hour from the 2nd to the end of the month must agree on one
        boundary. If it moved, the sweep would re-fire when it crossed."""
        seen = {
            sweep_window_start(_warsaw(2026, 9, day, hour))
            for day in range(AUDIT_DAY_OF_MONTH, 31) for hour in (0, 7, 13, 23)
        }
        self.assertEqual(len(seen), 1, f"boundary moved mid-month: {seen}")


class CalendarTriggerTest(unittest.TestCase):
    """The four behaviours Jacob asked for, stated as tests."""

    def test_it_fires_on_the_2nd(self):
        last = _warsaw(2026, 8, 20, 14, 0)          # swept last month
        first_tick = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, 0, 5)
        self.assertTrue(_due_on(first_tick, last))

    def test_it_does_not_fire_on_the_1st(self):
        last = _warsaw(2026, 8, 20, 14, 0)
        self.assertFalse(_due_on(_warsaw(2026, 9, 1, 23, 0), last))

    def test_it_fires_once_on_the_2nd_and_not_for_the_other_23_ticks(self):
        """The driver ticks hourly. Exactly one of those 24 ticks may sweep.

        The sweep's own `started_at` is what closes the window; there is no
        counter and nothing to reset, which is the reason this holds.
        """
        last = _warsaw(2026, 8, 20, 14, 0)
        fired = []
        for hour in range(24):
            now = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, hour)
            if _due_on(now, last):
                fired.append(hour)
                last = now          # the sweep runs and records itself
        self.assertEqual(fired, [0], f"swept at hours {fired}, expected only 0")

    def test_it_does_not_re_fire_on_the_3rd_after_having_run(self):
        ran = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, 0, 5)
        for day in range(AUDIT_DAY_OF_MONTH + 1, 31):
            self.assertFalse(_due_on(_warsaw(2026, 9, day, 12), ran),
                             f"re-fired on the {day}th")

    def test_a_missed_2nd_fires_late_rather_than_skipping_the_month(self):
        """Backend down all day on the 2nd, back on the 5th.

        A `day == 2` match would skip silently until October. Catch-up is the
        deliberate choice: a sweep three days late is stale, a sweep that never
        runs is the empty-findings failure the scheduler exists to end.
        """
        last = _warsaw(2026, 8, AUDIT_DAY_OF_MONTH, 3, 0)
        self.assertTrue(_due_on(_warsaw(2026, 9, 5, 9, 0), last))

    def test_the_catch_up_sweep_also_closes_the_window(self):
        """Firing late must not then fire again every hour for the rest of the
        month — the late run's own timestamp is after the boundary."""
        caught_up = _warsaw(2026, 9, 5, 9, 0)
        self.assertFalse(_due_on(_warsaw(2026, 9, 5, 10, 0), caught_up))
        self.assertFalse(_due_on(_warsaw(2026, 9, 28, 10, 0), caught_up))

    def test_the_next_month_reopens_the_window(self):
        ran = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, 0, 5)
        self.assertTrue(_due_on(_warsaw(2026, 10, AUDIT_DAY_OF_MONTH, 0, 5), ran))

    def test_the_ten_report_arm_still_fires_mid_month(self):
        """The burst trigger is untouched: ten new reports on the 17th sweeps
        without waiting for the calendar."""
        ran = _warsaw(2026, 9, AUDIT_DAY_OF_MONTH, 0, 5)
        mid_month = _warsaw(2026, 9, 17, 11, 0)
        self.assertFalse(_due_on(mid_month, ran, new_reports=AUDIT_EVERY_N_REPORTS - 1))
        self.assertTrue(_due_on(mid_month, ran, new_reports=AUDIT_EVERY_N_REPORTS))

    def test_shipping_this_does_not_trigger_a_sweep_on_the_next_tick(self):
        """Measured against production: the only completed run is
        2026-08-29 20:43 UTC and the corpus grew by one (14 -> 15) since. On the
        first tick after this ships, both arms must answer no — a deploy is not
        a cadence.
        """
        last = datetime(2026, 8, 29, 20, 43, tzinfo=timezone.utc)
        self.assertFalse(_due_on(_warsaw(2026, 8, 30, 9, 0), last, new_reports=1))

    def test_the_helper_matches_the_shipped_decision_logic(self):
        """`_due_on` above restates `audit_is_due`'s calendar arm. If the real
        function stops comparing `started_at` against `sweep_window_start`, this
        fails and the tests above stop being evidence about shipped code."""
        source = inspect.getsource(consistency_module.audit_is_due)
        self.assertIn("sweep_window_start(now)", source)
        self.assertIn('last["started_at"] < window_start', source)
        self.assertIn("new_reports >= AUDIT_EVERY_N_REPORTS", source)


if __name__ == "__main__":
    unittest.main()
