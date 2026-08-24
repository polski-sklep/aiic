"""Cross-agent numeric reconciliation.

reconcile_data is the system's only defence against eight independent agents
reporting mutually contradictory numbers. AIIC_HANDOFF section 9.5 records a
prior instance of exactly this class of bug ("Jaccard risk overlap = 0.000 ...
mismatched extraction shapes"). These tests ask whether the same shape mismatch
is still present.
"""
from __future__ import annotations

import unittest

from app.agents.reconciliation import build_case_context, reconcile_data

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


if __name__ == "__main__":
    unittest.main()
