"""Cross-report consistency sweep — the defects it must never reintroduce.

Every fixture below is either a live crash repro or verbatim text from the
sixteen persisted evaluations. Nothing here is invented prose.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.knowledge.consistency import (
    Claim,
    detect_conflicts,
    extract_claims,
    fingerprint_of,
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


if __name__ == "__main__":
    unittest.main()
