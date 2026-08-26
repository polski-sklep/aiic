"""Cross-agent numeric reconciliation.

reconcile_data is the system's only defence against eight independent agents
reporting mutually contradictory numbers. AIIC_HANDOFF section 9.5 records a
prior instance of exactly this class of bug ("Jaccard risk overlap = 0.000 ...
mismatched extraction shapes"). These tests ask whether the same shape mismatch
is still present.
"""
from __future__ import annotations

import unittest

from app.agents.reconciliation import (
    INTRA_RUN_RENDER_BUDGET,
    build_case_context,
    reconcile_data,
    render_contradictions,
)

CTX = {"case_time": "2026-08-24T00:00:00+00:00", "canonical_metrics": {}}


class ReconcileWorksTest(unittest.TestCase):
    def test_identical_paths_with_large_divergence_are_flagged(self):
        out = reconcile_data({"a": {"tvl": 100.0}, "b": {"tvl": 1000.0}}, CTX)
        self.assertEqual(out["inconsistencies_found"], 1)
        self.assertEqual(out["inconsistencies"][0]["metric"], "tvl")
        self.assertTrue(out["status"].startswith("WARNING"))

    def test_agreement_within_20_percent_is_clean(self):
        out = reconcile_data({"a": {"tvl": 100.0}, "b": {"tvl": 110.0}}, CTX)
        self.assertEqual(out["status"], "CLEAN")

    def test_single_reporter_is_never_an_inconsistency(self):
        self.assertEqual(reconcile_data({"a": {"tvl": 100.0}}, CTX)["inconsistencies_found"], 0)

    def test_non_dict_agent_output_is_skipped_not_fatal(self):
        out = reconcile_data({"a": "the agent errored", "b": {"tvl": 1.0}}, CTX)
        self.assertEqual(out["status"], "CLEAN")

    def test_inconsistency_list_is_capped_but_the_count_is_not(self):
        outputs = {f"agent_{i}": {"tvl": 10.0 ** i} for i in range(8)}
        out = reconcile_data(outputs, CTX)
        self.assertLessEqual(len(out["inconsistencies"]), 10)
        self.assertGreater(out["inconsistencies_found"], 10)


class ReconcileDefectsTest(unittest.TestCase):
    def test_QA_019_the_same_metric_under_different_nesting_must_be_compared(self):
        """QA-019 (HIGH): metrics are grouped by their full flattened path.

        _flatten produces dotted paths ("metrics.tvl", "data.tvl") and
        _group_metric_key only lowercases and strips underscores from that whole
        path. Two agents therefore have to agree on the exact nesting *and* the
        exact key spelling before any comparison happens.

        The eight data agents are independent by design (CONTRACTS section 4.2)
        and each shapes its own JSON. In practice nothing is ever compared and
        reconcile_data reports CLEAN for every evaluation. This is the same
        failure as the Jaccard-overlap-0.000 bug in handoff section 9.5.
        """
        out = reconcile_data(
            {
                "onchain_analyst": {"metrics": {"tvl": 100_000_000.0}},
                "tokenomics_analyst": {"protocol_data": {"tvl": 900_000_000.0}},
            },
            CTX,
        )
        self.assertEqual(out["inconsistencies_found"], 1, "a 9x TVL disagreement was reported as CLEAN")

    def test_QA_020_flagging_must_not_depend_on_agent_ordering(self):
        """QA-020 (HIGH): _relative_divergence is |a-b|/a -- asymmetric.

        Which value lands in ``a`` is the iteration order of the agent_outputs
        dict, i.e. the order the orchestrator happened to gather results in. The
        pair (100, 125) is 25% divergent one way and 20% the other, so it trips
        the >0.2 threshold in one order and not the other. The same evaluation
        run twice can report different inconsistency counts.
        """
        forward = reconcile_data({"a": {"tvl": 100.0}, "b": {"tvl": 125.0}}, CTX)["inconsistencies_found"]
        reverse = reconcile_data({"b": {"tvl": 125.0}, "a": {"tvl": 100.0}}, CTX)["inconsistencies_found"]
        self.assertEqual(forward, reverse, f"order-dependent: {forward} vs {reverse}")

    def test_QA_021_booleans_must_not_be_treated_as_metric_values(self):
        """QA-021 (MED): ``isinstance(val, (int, float))`` is True for bool.

        A flag like {"tvl_verified": true} is extracted as the number 1 and
        compared against another agent's real TVL, producing a 499,999,900%
        "inconsistency". Noise of that magnitude buries any real disagreement in
        the 10-item cap.
        """
        out = reconcile_data(
            {"a": {"tvl_verified": True}, "b": {"tvl_verified": 5_000_000.0}}, CTX
        )
        self.assertEqual(out["inconsistencies_found"], 0)

    def test_QA_022_common_market_cap_spellings_must_be_grouped(self):
        """QA-022 (MED): _extract_metrics whitelists the literal "market_cap".

        "marketCap" and "marketcap" are not extracted at all, so an agent using
        either spelling is invisible to reconciliation rather than merely
        ungrouped.
        """
        out = reconcile_data(
            {"a": {"market_cap": 1_000_000.0}, "b": {"marketCap": 900_000_000.0}}, CTX
        )
        self.assertEqual(out["inconsistencies_found"], 1)

    def test_QA_022_numeric_strings_must_be_reconciled(self):
        """QA-022 (MED): LLMs routinely emit numbers as strings.

        "100" and "99999" are both ignored, so the check is silently skipped for
        any agent that quotes its figures.
        """
        out = reconcile_data({"a": {"tvl": "100"}, "b": {"tvl": "99999"}}, CTX)
        self.assertEqual(out["inconsistencies_found"], 1)

    def test_QA_022_metrics_nested_in_lists_must_be_reconciled(self):
        """QA-022 (MED): _flatten descends into dicts only, never lists.

        A per-chain breakdown -- the natural shape for TVL -- is invisible.
        """
        out = reconcile_data(
            {"a": {"chains": [{"tvl": 1.0}]}, "b": {"chains": [{"tvl": 9999.0}]}}, CTX
        )
        self.assertEqual(out["inconsistencies_found"], 1)


class CaseContextTest(unittest.TestCase):
    def test_canonical_metrics_prefer_price_data_then_token_data(self):
        ctx = build_case_context(
            "Aave",
            {
                "_price_data": {"price": 63.0, "market_cap": None, "volume_24h": None},
                "_token_data": {"market_cap_usd": 950_000_000, "total_volume_usd": 1_000_000},
            },
        )
        self.assertEqual(ctx["canonical_metrics"]["price_usd"], 63.0)
        self.assertEqual(ctx["canonical_metrics"]["market_cap_usd"], 950_000_000)
        self.assertEqual(ctx["canonical_metrics"]["volume_24h_usd"], 1_000_000)

    def test_missing_prefetch_keys_yield_all_none_metrics(self):
        ctx = build_case_context("Aave", {})
        self.assertTrue(all(v is None for v in ctx["canonical_metrics"].values()))
        self.assertEqual(ctx["project_name"], "Aave")

    def test_QA_023_null_prefetch_blocks_must_not_crash(self):
        """QA-023 (MED): same ``.get(key, {})`` vs explicit None defect as QA-015.

        build_case_context runs before the agents; an AttributeError here aborts
        the whole evaluation rather than degrading to an unknown-metrics baseline.
        """
        build_case_context("Aave", {"_price_data": None, "_token_data": None})


# ---------------------------------------------------------------------------
# Within-run prose reconciliation
# ---------------------------------------------------------------------------

#: Verbatim from the live GMX evaluation 8e4b3c83 (2026-08-25). The report
#: writer put GMX's 30-day volume at $3,341,200 in one section and ~$2.8B two
#: sections later, and the structured check saw neither number because neither
#: is a structured field. Trimmed to the sentences that carry the figures;
#: nothing is reworded.
GMX_SECTION_5 = (
    "TVL is ~$300M total as of mid-2026, with Arbitrum ~$198M (94.2% concentration), "
    "staking ~$45M and Avalanche ~$11M. "
    "24h volume ~$2.9M against $75M MCap = ~3.9% daily volume/MCap — low liquidity. "
    "Daily trading volume runs ~$100-200M (perp.wiki). "
    "Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days."
)
GMX_SECTION_7 = (
    "Hyperliquid commands 70-80%+ of on-chain perp volume (44% as of Jan 2026 "
    "rising through mid-2026) with ~$6.66B TVL and processes ~$30B+ daily / "
    "~$245B over 30 days, versus GMX's ~$2.8B 30-day volume — an ~87x volume gap."
)
GMX_CTX = {
    "case_time": "2026-08-25T18:24:43+00:00",
    "project_name": "GMX",
    "project_aliases": ["gmx"],
    "canonical_metrics": {},
}
GMX_RUN = {
    "competitive_intel": {
        "key_findings": ["GMX's 30-day volume is approximately $2.8B vs. Hyperliquid's ~$245B."]
    },
    "report_writer": {
        "sections": {"5_on_chain_metrics": GMX_SECTION_5, "7_competitive_landscape": GMX_SECTION_7}
    },
}


class IntraRunContradictionTest(unittest.TestCase):
    """The acceptance case, and the near-misses that must stay quiet."""

    def test_the_report_writer_contradicting_itself_is_found(self):
        out = reconcile_data(GMX_RUN, GMX_CTX, "full_run")
        self.assertEqual(out["contradictions_found"], 1, out["status"])
        finding = out["contradictions"][0]
        self.assertEqual(finding["entity"], "GMX")
        self.assertEqual(finding["metric"], "volume_30d_usd")
        values = {c["value"] for c in finding["claims"]}
        self.assertIn("$3,341,200", values)
        self.assertTrue({"~$2.8B", "approximately $2.8B"} & values, values)

    def test_the_odd_one_out_is_named_and_it_is_the_report_writer(self):
        """"These disagree" is not actionable; "section 5 wrote it" is."""
        finding = reconcile_data(GMX_RUN, GMX_CTX, "full_run")["contradictions"][0]
        self.assertEqual(finding["outlier"]["value"], "$3,341,200")
        self.assertIn("report_writer", finding["outlier"]["source"])
        self.assertIn("5_on_chain_metrics", finding["outlier"]["source"])

    def test_one_agent_can_contradict_itself_across_two_sections(self):
        """The Report Writer alone, with no other agent in the run.

        This is the structural half of the defect: `reconcile_data` ran before
        the Report Writer existed, and its cross-agent framing would have
        skipped a single agent's output even if it had.
        """
        out = reconcile_data({"report_writer": GMX_RUN["report_writer"]}, GMX_CTX, "full_run")
        self.assertEqual(out["contradictions_found"], 1, out["status"])

    def test_the_same_section_never_contradicts_itself(self):
        """Two figures in one section are a parse question, not a finding."""
        one_section = {"report_writer": {"sections": {"5_on_chain_metrics": GMX_SECTION_5}}}
        self.assertEqual(
            reconcile_data(one_section, GMX_CTX, "full_run")["contradictions_found"], 0
        )

    def test_prose_extraction_is_not_optional_theatre(self):
        """The measured yield gap: 5 structured numbers, dozens of prose claims."""
        out = reconcile_data(GMX_RUN, GMX_CTX, "full_run")
        self.assertGreater(out["prose_claims_extracted"], 5)
        self.assertGreaterEqual(out["prose_agents_with_claims"], 2)


class IntraRunPrecisionTest(unittest.TestCase):
    """Every case here is a real near-miss from the corpus. All must be silent."""

    def _found(self, run, ctx=None):
        return reconcile_data(run, ctx or GMX_CTX, "full_run")["contradictions_found"]

    def test_different_denominators_are_not_a_contradiction(self):
        """GMX token spot volume ~$3M/day against GMX protocol perp volume
        ~$150M/day. Both true, 50x apart, and the single most dangerous false
        positive available — it is the shape of most real disagreements."""
        run = {
            "risk_officer": {
                "evidence": "GMX trades on Binance and numerous CEX/DEX venues "
                            "with ~$3M aggregate 24h volume."
            },
            "onchain_analyst": {
                "key_findings": ["Hyperliquid processes $30B+ daily vs GMX's "
                                 "estimated $100-200M daily."]
            },
        }
        self.assertEqual(self._found(run), 0)

    def test_the_same_quantity_at_two_dates_is_a_time_series(self):
        run = {
            "field_intel": {"key_findings": ["Hyperliquid held 36.4% of perp volume in January 2026."]},
            "competitive_intel": {"key_findings": ["Hyperliquid holds ~44% of perp volume as of mid-2026."]},
        }
        self.assertEqual(self._found(run), 0)

    def test_overlapping_hedged_ranges_agree(self):
        run = {
            "competitive_intel": {"summary": "GMX's 30-day volume is ~$2.8B this period."},
            "field_intel": {"summary": "GMX's 30-day volume is $2.5-3B this period."},
        }
        self.assertEqual(self._found(run), 0)

    def test_a_figure_and_its_component_are_not_a_contradiction(self):
        """GMX TVL ~$300M in total, $174.88M for V2 Perps alone."""
        run = {
            "onchain_analyst": {"summary": "GMX TVL is ~$300M across all chains."},
            "tech_infra_analyst": {"summary": "DeFiLlama puts GMX V2 Perps TVL at $174.88M."},
        }
        self.assertEqual(self._found(run), 0)

    def test_a_share_price_is_not_a_daily_volume(self):
        """"GMX is in a daily uptrend, trading at $7.20" — verbatim from
        technical_analyst. Adjacency alone binds "daily" to $7.20 and produces a
        20,833,333x finding that no magnitude threshold can filter."""
        run = {
            "technical_analyst": {
                "summary": "GMX is in a daily uptrend, trading at $7.20 — above all "
                           "three major EMAs and near the upper boundary of its range."
            },
            "onchain_analyst": {"key_findings": ["GMX's daily volume is $100-200M."]},
        }
        self.assertEqual(self._found(run), 0)

    def test_an_unresolvable_entity_is_dropped_not_guessed(self):
        """Attributing a stray figure to the report's own subject is how a
        third party's number becomes a claim about the project."""
        run = {
            "a": {"summary": "Some unnamed protocol reports 30-day volume of $4."},
            "b": {"summary": "Another one reports 30-day volume of $9,000,000,000."},
        }
        self.assertEqual(self._found(run), 0)


class IntraRunSafetyTest(unittest.TestCase):
    def test_a_clean_run_renders_nothing_at_all(self):
        """Zero tokens on the common case, or the block is not worth having."""
        out = reconcile_data({"a": {"summary": "GMX TVL is ~$300M."}}, GMX_CTX, "full_run")
        self.assertEqual(out["status"], "CLEAN")
        self.assertEqual(render_contradictions(out), "")

    def test_the_rendered_block_names_both_figures_and_their_sources(self):
        text = render_contradictions(reconcile_data(GMX_RUN, GMX_CTX, "full_run"))
        self.assertIn("$3,341,200", text)
        self.assertIn("report_writer sections.5_on_chain_metrics", text)
        self.assertIn("competitive_intel", text)

    def test_the_rendered_block_is_hard_capped(self):
        text = render_contradictions(reconcile_data(GMX_RUN, GMX_CTX, "full_run"))
        self.assertLessEqual(len(text), INTRA_RUN_RENDER_BUDGET)

    def test_reconciliation_can_never_fail_an_evaluation(self):
        """It is a guard. Nothing downstream needs it to have succeeded, and a
        run that dies here has lost fifteen agents of paid model calls."""
        for junk in ({}, {"case_time": "not-a-date", "project_name": None}, {"project_aliases": 7}):
            out = reconcile_data({"a": {"summary": "GMX TVL is ~$300M."}}, junk, "full_run")
            self.assertIn("status", out)

    def test_malformed_agent_output_is_skipped_not_fatal(self):
        run = {"a": None, "b": "just a string", "c": ["a", "list"], "d": GMX_RUN["report_writer"]}
        reconcile_data(run, GMX_CTX, "full_run")

    def test_the_structured_path_still_works_alongside_the_prose_one(self):
        """QA-019 through QA-023 bought that path; prose is added, not swapped."""
        out = reconcile_data({"a": {"tvl": 100.0}, "b": {"tvl": 1000.0}}, GMX_CTX, "full_run")
        self.assertEqual(out["inconsistencies_found"], 1)
        self.assertTrue(out["status"].startswith("WARNING"))


if __name__ == "__main__":
    unittest.main()
