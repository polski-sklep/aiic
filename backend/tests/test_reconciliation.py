"""Cross-agent numeric reconciliation.

reconcile_data is the system's only defence against eight independent agents
reporting mutually contradictory numbers. AIIC_HANDOFF section 9.5 records a
prior instance of exactly this class of bug ("Jaccard risk overlap = 0.000 ...
mismatched extraction shapes"). These tests ask whether the same shape mismatch
is still present.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

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
#
# THE FIRST VERSION OF THIS SUITE ASSERTED A CONTRADICTION THAT DOES NOT EXIST.
#
# It was built on the GMX report's "Buybacks: 103,764 GMX ($3,341,200) purchased
# over 30 days" being a 30-day trading volume in conflict with the same report's
# ~$2.8B. It is a buyback. $3.34M of buybacks and $2.8B of volume disagree about
# nothing, and every test that asserted the catch was asserting an extraction
# defect. The defect is fixed in `consistency.binding_is_sound` and pinned below in
# MisbindingRegressionTest, which now asserts the opposite of what those tests
# asserted.
#
# Fixtures below are verbatim corpus text. The acceptance case is Aave
# evaluation c1479a94 (2026-04-11), where three agents put Aave's TVL at $25.7B
# "across 20+ chains" and three others put it at $61.9B "across 20+ chains".

AAVE_CTX = {
    "case_time": "2026-04-11T17:52:22+00:00",
    "project_name": "Aave",
    "project_aliases": ["AAVE", "aave"],
    "canonical_metrics": {},
}
GMX_CTX = {
    "case_time": "2026-08-25T18:24:43+00:00",
    "project_name": "GMX",
    "project_aliases": ["gmx"],
    "canonical_metrics": {},
}

#: Verbatim from evaluation c1479a94. Six agents, two irreconcilable values, the
#: same "across 20+ chains" qualifier on both.
AAVE_RUN = {
    "competitive_intel": {
        "summary": "Aave dominates DeFi lending with $25.7B TVL (62% market share)."
    },
    "onchain_analyst": {
        "summary": "Aave shows strong on-chain fundamentals with $25.7B TVL across 20+ chains."
    },
    # Verbatim. The entity resolves from "AAVE" earlier in the same sentence —
    # this agent's key_findings line ("Multi-chain protocol dominance with
    # $25.6B TVL across 20+ chains") names none and is correctly dropped.
    "tokenomics_analyst": {
        "value_accrual_assessment":
            "Strong value accrual mechanisms through multiple channels: (1) Safety "
            "Module staking with $275M+ staked AAVE earning rewards and providing "
            "protocol backstop, (2) Governance rights over a $25.6B TVL protocol"
    },
    "report_writer": {
        "sections": {
            "1_executive_summary": "Aave is the dominant DeFi lending protocol with "
                                   "$25.7B TVL across 20+ chains."
        }
    },
    "governance_analyst": {
        "key_findings": [
            "Stani Kulechov has proven execution track record, successfully "
            "building ETHLend into Aave with $61.9B TVL across 20+ chains"
        ]
    },
    "maturation_scorer": {
        "summary": "Aave demonstrates exceptional maturity as a DeFi blue chip with "
                   "7+ years of operation, $61.9B TVL across 20+ chains."
    },
    "risk_officer": {
        "key_findings": ["Aave: $61.9B TVL across 20+ chains demonstrates protocol maturity"]
    },
}


class MisbindingRegressionTest(unittest.TestCase):
    """Figures the extractor binds to the wrong metric. None may reach a finding.

    Each fixture is verbatim corpus text and each was a live false positive.
    """

    def _claims(self, run, ctx):
        out = reconcile_data(run, ctx, "full_run")
        return out

    def test_a_buyback_is_not_a_thirty_day_volume(self):
        """The case the first version of this module was built on, inverted.

        "over 30 days" sits thirteen characters from $3,341,200 with no digit
        between, so adjacency alone binds it. The figure is 103,764 GMX restated
        in dollars, and the governing noun is "Buybacks:".
        """
        run = {
            "report_writer": {
                "sections": {
                    "5_on_chain_metrics":
                        "Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days; "
                        "168,500 GMX buybacks completed alongside the CEO appointment.",
                    "7_competitive_landscape":
                        "Hyperliquid processes ~$245B over 30 days, versus GMX's "
                        "~$2.8B 30-day volume — an ~87x volume gap.",
                }
            },
            "competitive_intel": {
                "key_findings": ["GMX's 30-day volume is approximately $2.8B vs Hyperliquid's ~$245B."]
            },
        }
        out = self._claims(run, GMX_CTX)
        self.assertEqual(out["contradictions_found"], 0, render_contradictions(out))
        self.assertEqual(out["uncorroborated_candidates"], [])

    def test_a_vesting_tranche_priced_at_fdv_is_not_the_fdv(self):
        """"23.8% of total supply (238M HYPE, ~$19.4B FDV)" — verbatim.

        The same agent says elsewhere, correctly, "FDV ($78B) is 4.3x market cap
        ($18.2B)", so a naive read makes it contradict itself 4x.
        """
        run = {
            "legal_regulatory": {
                "risks": [
                    "Ongoing rolling Hyperliquid core contributor unlocks through 2027-2028: "
                    "23.8% of total supply (238M HYPE, ~$19.4B FDV) vesting linearly",
                    "Structural: Hyperliquid FDV ($78B) is 4.3x market cap ($18.2B).",
                ]
            },
            "field_intel": {
                "summary": "HYPE is trading near ATH at ~$81.73, placing Hyperliquid "
                           "at a ~$78B FDV — near full price-discovery."
            },
        }
        out = self._claims(run, {**GMX_CTX, "project_name": "Hyperliquid"})
        self.assertEqual(out["contradictions_found"], 0, render_contradictions(out))

    def test_a_percentage_of_a_metric_is_not_the_metric(self):
        """"~2.7% of market cap (~$1.16B)" — the shape consistency.py's own
        docstring records, in the one form its no-digit rule cannot see: the
        digits sit before the label, not between the label and the figure."""
        run = {
            "field_intel": {
                "key_findings": [
                    "NEXT UNLOCK: ~14.175M HYPE tokens unlock August 29, 2026 (4 days), "
                    "representing 1.4% of total supply and ~2.7% of market cap (~$1.16B)."
                ]
            },
            "legal_regulatory": {
                "key_findings": ["HYPE is currently ranked #10 by market cap (~$18.2B)."]
            },
        }
        out = self._claims(run, {**GMX_CTX, "project_name": "Hyperliquid"})
        self.assertEqual(out["contradictions_found"], 0, render_contradictions(out))

    def test_a_share_price_is_not_a_daily_volume(self):
        """"GMX is in a daily uptrend, trading at $7.20" — "daily" labels the
        uptrend. Unfiltered this is a 20,833,333x finding."""
        run = {
            "technical_analyst": {
                "summary": "GMX is in a daily uptrend, trading at $7.20 — above all "
                           "three major EMAs and near the upper boundary of its range."
            },
            "onchain_analyst": {"key_findings": ["GMX's daily volume is $100-200M."]},
            "field_intel": {"summary": "GMX V2 daily volume is ~$100-200M."},
        }
        out = self._claims(run, GMX_CTX)
        self.assertEqual(out["contradictions_found"], 0, render_contradictions(out))

    def test_the_labels_that_must_survive_all_of_the_above(self):
        """The rules above are aggressive. These ordinary forms must still bind."""
        run = {
            "a": {"summary": "Aave's market cap ($75M) is small relative to peers."},
            "b": {"summary": "Aave market cap is approximately $9.4B as of today."},
        }
        out = reconcile_data(run, AAVE_CTX, "full_run")
        self.assertGreaterEqual(out["prose_claims_extracted"], 2,
                                "ordinary 'metric ($value)' phrasing stopped binding")


class CorroboratedSplitTest(unittest.TestCase):
    """The acceptance case: two camps, each with independent backing."""

    def test_the_aave_tvl_split_is_found(self):
        out = reconcile_data(AAVE_RUN, AAVE_CTX, "full_run")
        self.assertEqual(out["contradictions_found"], 1, out["status"])
        finding = out["contradictions"][0]
        self.assertEqual(finding["entity"], "Aave")
        self.assertEqual(finding["metric"], "tvl_usd")
        values = {camp["value"] for camp in finding["camps"]}
        self.assertEqual(values, {"$25.7B", "$61.9B"}, values)

    def test_both_camps_name_their_backing_agents(self):
        finding = reconcile_data(AAVE_RUN, AAVE_CTX, "full_run")["contradictions"][0]
        backing = {camp["value"]: set(camp["agents"]) for camp in finding["camps"]}
        for value, agents in backing.items():
            self.assertGreaterEqual(len(agents), 2, f"{value} reported with one agent")
        self.assertIn("governance_analyst", set().union(*backing.values()))

    def test_near_values_merge_into_one_camp(self):
        """$25.7B and $25.6B are one belief stated twice, not two camps."""
        finding = reconcile_data(AAVE_RUN, AAVE_CTX, "full_run")["contradictions"][0]
        self.assertEqual(len(finding["camps"]), 2)

    def test_a_camp_is_labelled_with_what_its_agents_actually_wrote(self):
        """The camp holds four "$25.7B" and one "$25.6B". It must render
        "$25.7B" — the value four of its five agents wrote.

        Labelling it from the first member returned "$25.6B", attributing to
        competitive_intel, onchain_analyst and report_writer a figure only
        tokenomics_analyst had written. A block that exists to quote agents back
        to the Chair may not misquote them, least of all while flagging the
        report for misattributed figures.
        """
        out = reconcile_data(AAVE_RUN, AAVE_CTX, "full_run")
        camps = {camp["value"]: camp for camp in out["contradictions"][0]["camps"]}
        self.assertIn("$25.7B", camps)
        self.assertNotIn("$25.6B", camps)
        self.assertIn("tokenomics_analyst", camps["$25.7B"]["agents"])
        self.assertIn("$25.7B  —", render_contradictions(out))
        self.assertNotIn("$25.6B", render_contradictions(out))

    def test_there_is_no_magnitude_gate(self):
        """A corroborated 2.4x split reports. The check was rebuilt precisely
        because magnitude ranked the corpus's false positive (50x) above its
        true one (2.4x)."""
        finding = reconcile_data(AAVE_RUN, AAVE_CTX, "full_run")["contradictions"][0]
        self.assertLess(finding["ratio"], 3.0)

    def test_the_report_writer_contradicting_itself_is_called_out(self):
        """The shape this pass exists for: one agent on both sides of a split.

        Synthetic, because the real instance is invisible to the extractor — in
        c1479a94 the Report Writer does use $61.9B in section 2, but that
        sentence ("The protocol ... with $61.9B in total value locked") names no
        entity, and the extractor drops an unattributable claim rather than
        defaulting it to the report's own subject.
        """
        run = dict(AAVE_RUN)
        run["report_writer"] = {
            "sections": {
                "1_executive_summary": "Aave is the dominant lending protocol with "
                                       "$25.7B TVL across 20+ chains.",
                "2_project_overview": "Aave has become the market leader in DeFi "
                                      "lending with $61.9B TVL across many chains.",
            }
        }
        finding = reconcile_data(run, AAVE_CTX, "full_run")["contradictions"][0]
        self.assertIn("report_writer", finding["self_contradicting_agents"])
        self.assertIn("report_writer", render_contradictions(
            reconcile_data(run, AAVE_CTX, "full_run")))


class UncorroboratedIsSilentTest(unittest.TestCase):
    """A lone dissenter is recorded, never rendered. Every fixture is real."""

    def _out(self, run, ctx=None):
        return reconcile_data(run, ctx or GMX_CTX, "full_run")

    def test_a_lone_scope_difference_is_not_reported(self):
        """~$3M is GMX-the-token across CEX/DEX venues; ~$150M is V2 perp
        notional. 50x apart, both true, and the largest gap in the corpus."""
        run = {
            "risk_officer": {
                "evidence": "GMX trades on Binance and numerous CEX/DEX venues with "
                            "~$3M aggregate 24h volume."
            },
            "onchain_analyst": {"key_findings": ["GMX's estimated daily volume is $100-200M."]},
            "report_writer": {"sections": {"7": "GMX's daily volume is $100-200M."}},
        }
        out = self._out(run)
        self.assertEqual(out["contradictions_found"], 0)
        self.assertEqual(render_contradictions(out), "")

    def test_the_suppressed_candidate_is_still_recorded(self):
        """Silent to the Chair, visible in the record. The evidence for
        revisiting the corroboration rule must not be thrown away."""
        run = {
            "risk_officer": {"evidence": "GMX has ~$3M aggregate 24h volume."},
            "onchain_analyst": {"key_findings": ["GMX's daily volume is $100-200M."]},
            "report_writer": {"sections": {"7": "GMX's daily volume is $100-200M."}},
        }
        out = self._out(run)
        self.assertEqual(len(out["uncorroborated_candidates"]), 1)
        self.assertIn("agents", out["uncorroborated_candidates"][0]["values"][0])

    def test_one_agent_restating_itself_is_one_voice(self):
        """Four sections of the Report Writer are not four corroborations."""
        run = {
            "report_writer": {
                "sections": {
                    "1": "Aave TVL is $25.7B across 20+ chains.",
                    "2": "Aave TVL is $25.7B across 20+ chains.",
                    "3": "Aave TVL is $25.7B across 20+ chains.",
                    "5": "Aave TVL is $61.9B across 20+ chains.",
                }
            }
        }
        self.assertEqual(self._out(run, AAVE_CTX)["contradictions_found"], 0)


class IntraRunPrecisionTest(unittest.TestCase):
    """Near-misses that must stay silent even when both sides are corroborated."""

    def _found(self, run, ctx=None):
        return reconcile_data(run, ctx or GMX_CTX, "full_run")["contradictions_found"]

    def test_the_same_quantity_at_two_dates_is_a_time_series(self):
        run = {
            "field_intel": {"key_findings": ["Hyperliquid held 36.4% of perp volume in January 2026."]},
            "onchain_analyst": {"risks": ["Hyperliquid held 36.4% of perp volume in January 2026."]},
            "competitive_intel": {"key_findings": ["Hyperliquid holds ~44% of perp volume as of mid-2026."]},
            "devils_advocate": {"summary": "Hyperliquid holds ~44% of perp volume as of mid-2026."},
        }
        self.assertEqual(self._found(run), 0)

    def test_overlapping_hedged_ranges_agree(self):
        run = {
            "competitive_intel": {"summary": "GMX's 30-day volume is ~$2.8B this period."},
            "onchain_analyst": {"summary": "GMX's 30-day volume is ~$2.8B this period."},
            "field_intel": {"summary": "GMX's 30-day volume is $2.5-3B this period."},
            "devils_advocate": {"summary": "GMX's 30-day volume is $2.5-3B this period."},
        }
        self.assertEqual(self._found(run), 0)

    def test_an_unresolvable_entity_is_dropped_not_guessed(self):
        run = {
            "a": {"summary": "Some unnamed protocol reports 30-day volume of $4,000."},
            "b": {"summary": "Another one reports 30-day volume of $9,000,000,000."},
            "c": {"summary": "A third reports 30-day volume of $4,000."},
            "d": {"summary": "A fourth reports 30-day volume of $9,000,000,000."},
        }
        self.assertEqual(self._found(run), 0)

    def test_two_figures_in_one_section_never_contradict(self):
        """A sentence disagreeing with itself is a parse question, not a finding."""
        run = {
            "report_writer": {
                "sections": {"5": "Aave TVL is $25.7B across 20+ chains and Aave TVL is $61.9B."}
            }
        }
        self.assertEqual(self._found(run, AAVE_CTX), 0)


class IntraRunSafetyTest(unittest.TestCase):
    def test_a_clean_run_renders_nothing_at_all(self):
        """Zero tokens on the common case — 15 of 16 corpus runs — or the block
        is not worth having."""
        out = reconcile_data({"a": {"summary": "Aave TVL is ~$25.7B."}}, AAVE_CTX, "full_run")
        self.assertEqual(out["status"], "CLEAN")
        self.assertEqual(render_contradictions(out), "")

    def test_the_rendered_block_names_both_values_and_their_agents(self):
        text = render_contradictions(reconcile_data(AAVE_RUN, AAVE_CTX, "full_run"))
        self.assertIn("$61.9B", text)
        self.assertIn("governance_analyst", text)
        self.assertIn("onchain_analyst", text)

    def test_the_rendered_block_is_hard_capped(self):
        text = render_contradictions(reconcile_data(AAVE_RUN, AAVE_CTX, "full_run"))
        self.assertLessEqual(len(text), INTRA_RUN_RENDER_BUDGET)

    def test_reconciliation_can_never_fail_an_evaluation(self):
        """It is a guard. A run that dies here has lost fifteen agents of paid
        model calls to a warning system."""
        for junk in ({}, {"case_time": "not-a-date", "project_name": None}, {"project_aliases": 7}):
            out = reconcile_data({"a": {"summary": "Aave TVL is ~$25.7B."}}, junk, "full_run")
            self.assertIn("status", out)

    def test_malformed_agent_output_is_skipped_not_fatal(self):
        run = {"a": None, "b": "just a string", "c": ["a", "list"], "d": AAVE_RUN["risk_officer"]}
        reconcile_data(run, AAVE_CTX, "full_run")

    def test_the_structured_path_still_works_alongside_the_prose_one(self):
        """QA-019 through QA-023 bought that path; prose is added, not swapped."""
        out = reconcile_data({"a": {"tvl": 100.0}, "b": {"tvl": 1000.0}}, AAVE_CTX, "full_run")
        self.assertEqual(out["inconsistencies_found"], 1)
        self.assertTrue(out["status"].startswith("WARNING"))

class SharedBindingRulesTest(unittest.TestCase):
    """The binding rules now live in `knowledge/consistency` and are imported.

    Two copies of a binding rule that drift apart is the failure this project
    keeps hitting, so there must be exactly one definition and this module must
    be using it.
    """

    def test_this_module_defines_no_binding_rules_of_its_own(self):
        from app.agents import reconciliation

        for name in (
            "_binding_is_sound", "_TRAILING_QUANTITY", "_DENOMINATOR_PREFIX",
            "_FOREIGN_QUANTITY", "_BACKWARD_CLAUSE_BREAK", "_locate_value",
            "_restates_a_preceding_quantity", "_own_metric_bindings", "_nearest_rival",
        ):
            self.assertFalse(
                hasattr(reconciliation, name),
                f"{name} is defined here as well as in knowledge/consistency",
            )

    def test_the_within_run_check_keeps_the_backward_reach_rule(self):
        """"trading at $7.20" is removed by the foreign-quantity rule, but the
        backward-reach rule is what this module measured and must keep asking
        for. The cross-report sweep deliberately does not."""
        from app.knowledge.consistency import binding_is_sound, extract_claims

        claims = extract_claims(
            "On market cap, Hyperliquid is ~$18.3B vs GMX $75M.",
            evaluation_id="e", report_project="GMX", section="s",
            report_date=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
        subject = [c for c in claims if c.raw == "~$18.3B"]
        self.assertEqual(len(subject), 1, "the sweep should keep this true claim")
        self.assertFalse(binding_is_sound(subject[0], reject_backward_reach=True))
        self.assertTrue(binding_is_sound(subject[0]))

if __name__ == "__main__":
    unittest.main()
