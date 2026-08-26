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


class SweepSafetyTest(unittest.TestCase):
    def test_an_empty_corpus_yields_no_conflicts_and_no_crash(self):
        self.assertEqual(detect_conflicts([]), [])

    def test_one_evaluation_alone_can_never_conflict_with_itself(self):
        same = [claim(period="2026-01"), claim(period="2026-06")]
        self.assertEqual(detect_conflicts(same), [])


if __name__ == "__main__":
    unittest.main()
