"""Run-cost estimation: app/llm/pricing.py, and the `_ser` fields it depends on.

The case this file exists to prevent is a plausible-looking wrong number. Since
prompt caching landed, ``tokens_input`` is the uncached remainder only, so
pricing ``tokens_input + tokens_output`` produces a figure that is smooth,
stable, roughly the right order of magnitude and less than half the truth. The
Dolphin numbers below are the real ones, and they pin both the correct answer
and the size of that error.
"""
from __future__ import annotations

import pathlib
import unittest

from app.llm import pricing


# Real per-agent token counts from evaluation 3c5483d5 (Dolphin, 2026-08-27,
# production). `tokens_input` and `tokens_output` are the persisted values from
# `agent_outputs`; the two cache columns do not exist in that table and were
# recovered from the backend's own per-round log lines, then cross-checked
# against the persisted in/out counts for every one of the fifteen agents.
#
#   (agent, model, uncached_input, cache_write, cache_read, output)
DOLPHIN = [
    ("committee_chair", "claude-opus-4-8", 4, 42035, 40490, 5937),
    ("competitive_intel", "claude-sonnet-4-6", 9, 16494, 67906, 4631),
    ("devils_advocate", "claude-opus-4-8", 10, 16106, 45607, 6677),
    ("field_intel", "claude-sonnet-4-6", 9, 15196, 61616, 3666),
    ("governance_analyst", "claude-sonnet-4-6", 9, 14541, 58687, 3770),
    ("legal_regulatory", "claude-sonnet-4-6", 11, 15996, 83999, 4281),
    ("maturation_scorer", "claude-opus-4-8", 8, 13182, 30294, 2740),
    ("onchain_analyst", "claude-sonnet-4-6", 10, 17139, 77748, 4787),
    ("portfolio_manager", "claude-opus-4-8", 6, 9742, 13987, 2425),
    ("ray_dalio", "claude-opus-4-8", 8, 18005, 45218, 4388),
    ("report_writer", "claude-opus-4-8", 4, 63782, 62286, 22228),
    ("risk_officer", "claude-opus-4-8", 12, 22834, 89602, 4644),
    ("tech_infra_analyst", "claude-sonnet-4-6", 15, 18583, 152498, 5026),
    ("technical_analyst", "claude-sonnet-4-6", 5, 6773, 12141, 2135),
    ("tokenomics_analyst", "claude-sonnet-4-6", 6, 7938, 14947, 2329),
]


def dolphin_results() -> dict:
    return {
        name: {
            "agent_name": name,
            "model_used": model,
            "tokens_input": tin,
            "cache_write_tokens": cw,
            "cache_read_tokens": cr,
            "tokens_output": tout,
            "error": None,
        }
        for name, model, tin, cw, cr, tout in DOLPHIN
    }


def naive_total(results: dict) -> float:
    """The tempting implementation this module exists to be correct instead of."""
    total = 0.0
    for record in results.values():
        rates = pricing.USD_PER_MTOK.get(pricing.normalise_model(record["model_used"]))
        if rates:
            total += record["tokens_input"] / 1e6 * rates[0]
            total += record["tokens_output"] / 1e6 * rates[1]
    return total


class PriceTableTest(unittest.TestCase):
    def test_table_carries_its_provenance(self):
        # An unlabelled float multiplied by a token count is how a wrong number
        # survives for months. The date and the source are part of the table.
        self.assertRegex(pricing.PRICES_TAKEN_ON, r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(pricing.PRICES_SOURCE.startswith("https://"))

    def test_published_rates(self):
        self.assertEqual(pricing.USD_PER_MTOK["claude-opus-5"], (5.00, 25.00))
        self.assertEqual(pricing.USD_PER_MTOK["claude-sonnet-5"], (2.00, 10.00))
        self.assertEqual(pricing.USD_PER_MTOK["claude-opus-4-8"], (5.00, 25.00))
        self.assertEqual(pricing.USD_PER_MTOK["claude-sonnet-4-6"], (3.00, 15.00))
        self.assertEqual(pricing.USD_PER_MTOK["claude-haiku-4-5"], (1.00, 5.00))
        self.assertEqual(pricing.CACHE_WRITE_MULTIPLIER, 1.25)
        self.assertEqual(pricing.CACHE_READ_MULTIPLIER, 0.10)

    def test_superseded_models_stay_priceable(self):
        # The 20 evaluations persisted before 2026-08-30 recorded
        # `claude-opus-4-8` and `claude-sonnet-4-6` in `agent_outputs.model_used`.
        # Pricing is applied at *read* time, so dropping either row when the
        # committee moved to Opus 5 / Sonnet 5 would retroactively turn every
        # one of those runs into "cost not available" — the exact wording the
        # April-2026 Plasma record already carries for its retired ids. This
        # test is the reason the table is append-only.
        for retired in ("claude-opus-4-8", "claude-sonnet-4-6"):
            cost = pricing.price_agent(
                "historic",
                {"model_used": retired, "tokens_input": 1000, "tokens_output": 1000},
            )
            self.assertTrue(cost.priced, "%s lost its published rate" % retired)
            self.assertGreater(cost.usd_total, 0.0)

    def test_every_configured_model_is_priceable(self):
        # config.py is what the pipeline actually runs on. If a model id there
        # is not in the table, every run using it degrades to "unpriced" —
        # correctly, but silently as far as anyone reading this repo goes.
        from app.config import Settings

        s = Settings()
        for attr in ("opus_model", "sonnet_model", "haiku_model"):
            model = getattr(s, attr)
            self.assertIn(
                pricing.normalise_model(model),
                pricing.USD_PER_MTOK,
                "config.%s = %r has no published rate" % (attr, model),
            )

    def test_dated_snapshot_ids_normalise(self):
        # config.py ships haiku as claude-haiku-4-5-20251001.
        self.assertEqual(
            pricing.normalise_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5"
        )
        self.assertEqual(
            pricing.normalise_model("claude-opus-4-8-20260101"), "claude-opus-4-8"
        )

    def test_normalisation_does_not_invent_a_match(self):
        # Stripping must not turn an unknown model into a known one.
        for unknown in ("claude-opus-9-9", "gpt-4o", "claude-opus-4-8-2026", ""):
            self.assertNotIn(pricing.normalise_model(unknown), pricing.USD_PER_MTOK)


class SingleAgentPricingTest(unittest.TestCase):
    def test_four_streams_priced_at_their_own_rates(self):
        cost = pricing.price_agent(
            "x",
            {
                "model_used": "claude-opus-4-8",
                "tokens_input": 1_000_000,
                "cache_write_tokens": 1_000_000,
                "cache_read_tokens": 1_000_000,
                "tokens_output": 1_000_000,
            },
        )
        self.assertAlmostEqual(cost.usd_uncached_input, 5.00)
        self.assertAlmostEqual(cost.usd_cache_write, 6.25)
        self.assertAlmostEqual(cost.usd_cache_read, 0.50)
        self.assertAlmostEqual(cost.usd_output, 25.00)
        self.assertAlmostEqual(cost.usd_total, 36.75)
        # Same tokens with no cache: all 3M prompt tokens at the input rate.
        self.assertAlmostEqual(cost.usd_without_cache, 15.00 + 25.00)

    def test_prompt_total_is_all_three_input_streams(self):
        cost = pricing.price_agent(
            "x",
            {
                "model_used": "claude-sonnet-4-6",
                "tokens_input": 15,
                "cache_write_tokens": 18583,
                "cache_read_tokens": 152498,
                "tokens_output": 5026,
            },
        )
        self.assertEqual(cost.tokens_prompt_total, 15 + 18583 + 152498)

    def test_unknown_model_with_tokens_is_flagged_not_zeroed(self):
        cost = pricing.price_agent(
            "x", {"model_used": "claude-opus-9-9", "tokens_output": 5000}
        )
        self.assertFalse(cost.priced)
        self.assertEqual(cost.usd_total, 0.0)
        self.assertIn("claude-opus-9-9", cost.unpriced_reason)

    def test_agent_that_spent_nothing_is_not_a_pricing_hole(self):
        # GMX's committee_chair died on an API 400 with model_used='' and zero
        # tokens. That costs zero whatever model it would have used; calling it
        # "unpriced" would put a false warning on an otherwise complete run.
        cost = pricing.price_agent(
            "committee_chair",
            {"model_used": "", "tokens_input": 0, "tokens_output": 0, "error": "400"},
        )
        self.assertTrue(cost.priced)
        self.assertEqual(cost.usd_total, 0.0)

    def test_missing_and_junk_fields_do_not_raise(self):
        for record in ({}, {"model_used": None}, {"tokens_input": "many"},
                       {"tokens_output": -5}, {"tokens_input": None}, object()):
            pricing.price_agent("x", record)  # must not raise


class DolphinRunTest(unittest.TestCase):
    """The real numbers, and the size of the error the naive sum would make."""

    def setUp(self):
        self.results = dolphin_results()
        self.cost = pricing.price_run(self.results)

    def test_totals(self):
        self.assertEqual(self.cost.agent_count, 15)
        self.assertTrue(self.cost.complete)
        self.assertAlmostEqual(self.cost.total_usd, 3.591449, places=5)
        self.assertAlmostEqual(self.cost.without_cache_usd, 6.178288, places=5)
        self.assertAlmostEqual(self.cost.cache_saving_usd, 2.586839, places=5)

    def test_real_prompt_is_four_orders_of_magnitude_above_tokens_input(self):
        self.assertEqual(sum(a.tokens_uncached_input for a in self.cost.agents), 126)
        self.assertEqual(sum(a.tokens_prompt_total for a in self.cost.agents), 1_155_498)

    def test_naive_sum_reports_under_half(self):
        naive = naive_total(self.results)
        self.assertAlmostEqual(naive, 1.685832, places=5)
        self.assertLess(naive, self.cost.total_usd / 2)

    def test_dropping_the_cache_fields_collapses_the_total(self):
        # If `_ser` ever stops propagating the two cache keys, this run prices
        # at the naive figure again. That regression must fail a test, not ship.
        stripped = {
            name: {k: v for k, v in rec.items()
                   if k not in ("cache_write_tokens", "cache_read_tokens")}
            for name, rec in self.results.items()
        }
        blind = pricing.price_run(stripped)
        self.assertFalse(blind.cache_fields_present)
        self.assertAlmostEqual(blind.total_usd, naive_total(self.results), places=5)

    def test_rendered_line(self):
        self.assertEqual(
            pricing.format_cost_line(self.cost),
            "Cost: ~$3.59 (list-price estimate, prompt cache saved ~$2.59)",
        )


class DegradationTest(unittest.TestCase):
    def test_partial_pricing_marks_the_total_as_a_floor(self):
        results = dolphin_results()
        results["report_writer"]["model_used"] = "claude-opus-9-9"
        cost = pricing.price_run(results)
        line = pricing.format_cost_line(cost)
        self.assertFalse(cost.complete)
        self.assertIn("+", line.splitlines()[0])
        self.assertIn("1 of 15 agents unpriced", line)
        self.assertIn("claude-opus-9-9", line)

    def test_nothing_priceable_says_so(self):
        results = dolphin_results()
        for record in results.values():
            record["model_used"] = "some-other-vendor"
        line = pricing.format_cost_line(pricing.price_run(results))
        self.assertTrue(line.startswith("Cost: not available"))
        self.assertNotIn("$0.00", line)

    def test_a_negligible_priced_share_never_leads_with_a_number(self):
        # Plasma (2026-04-12) is exactly this: its eight working agents ran on
        # retired April model ids, and the only agents this table can price are
        # the six that crashed before spending anything. "~$0.00+" would read
        # as a free run.
        results = {
            "worked": {"model_used": "claude-opus-4-20250514",
                       "tokens_input": 200000, "tokens_output": 8000},
            "crashed": {"model_used": "claude-opus-4-8",
                        "tokens_input": 0, "tokens_output": 0},
        }
        line = pricing.format_cost_line(pricing.price_run(results))
        self.assertTrue(line.startswith("Cost: not available"))
        self.assertNotIn("$0.00", line)

    def test_run_with_no_agents_emits_nothing(self):
        self.assertEqual(pricing.format_cost_line(pricing.price_run({})), "")
        self.assertEqual(pricing.format_cost_line(pricing.price_run(None)), "")

    def test_records_without_cache_keys_are_labelled(self):
        # Historical `agent_outputs` rows: the table has no cache columns.
        cost = pricing.price_run(
            {"a": {"model_used": "claude-opus-4-8",
                   "tokens_input": 100000, "tokens_output": 5000}}
        )
        self.assertFalse(cost.cache_fields_present)
        self.assertIn("prompt cache not recorded", pricing.format_cost_line(cost))

    def test_a_real_cost_never_renders_as_zero(self):
        cost = pricing.price_run(
            {"a": {"model_used": "claude-haiku-4-5", "tokens_output": 100,
                   "cache_read_tokens": 0, "cache_write_tokens": 0}}
        )
        self.assertGreater(cost.total_usd, 0.0)
        self.assertIn("<$0.01", pricing.format_cost_line(cost))


class SerialisationTest(unittest.TestCase):
    """`_ser` is the only path by which cache counts leave the orchestrator."""

    def test_ser_propagates_both_cache_fields(self):
        from app.agents.base import AgentResult
        from app.agents.orchestrator import Orchestrator

        result = AgentResult(
            agent_name="risk_officer",
            output={},
            model_used="claude-opus-4-8",
            tokens_input=12,
            tokens_output=4644,
            cache_write_tokens=22834,
            cache_read_tokens=89602,
        )
        serialised = Orchestrator._ser(Orchestrator.__new__(Orchestrator), result)
        self.assertEqual(serialised["cache_write_tokens"], 22834)
        self.assertEqual(serialised["cache_read_tokens"], 89602)
        # And the serialised shape is directly priceable — the bot receives
        # exactly this over the API.
        cost = pricing.price_run({"risk_officer": serialised})
        self.assertTrue(cost.complete)
        # 12/1e6*5.00 + 22834/1e6*6.25 + 89602/1e6*0.50 + 4644/1e6*25.00
        self.assertAlmostEqual(cost.total_usd, 0.3036735, places=6)


class StandaloneLoadabilityTest(unittest.TestCase):
    """telegram_bot.py loads pricing.py by path under Python 3.10.

    The bot runs on the VPS system interpreter (3.10.12) with neither the
    backend's dependencies nor its package layout usable: importing
    `app.llm.pricing` would execute app/utils/types.py, which needs
    TypeAliasType (3.12+). So pricing.py must stay standalone. A violation
    would not fail anything here — it would silently drop the cost line from
    every message on the only machine that sends them.
    """

    def source(self) -> str:
        return pathlib.Path(pricing.__file__).read_text(encoding="utf-8")

    def test_no_intra_package_imports(self):
        for line in self.source().splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith(("from app.", "import app.", "from app ")),
                "pricing.py must not import from the app package: %r" % line,
            )

    def test_only_standard_library_is_imported(self):
        allowed = {"re", "dataclasses", "__future__"}
        for line in self.source().splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                self.assertIn(stripped.split()[1].split(".")[0], allowed, stripped)
            elif stripped.startswith("from ") and " import " in stripped:
                self.assertIn(stripped.split()[1].split(".")[0], allowed, stripped)

    def test_loads_from_a_bare_spec_with_no_package_context(self):
        import importlib.util
        import sys

        name = "pricing_standalone_probe"
        spec = importlib.util.spec_from_file_location(name, pricing.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # required: @dataclass resolves via sys.modules
        try:
            spec.loader.exec_module(module)
            self.assertEqual(module.USD_PER_MTOK, pricing.USD_PER_MTOK)
            self.assertTrue(module.format_cost_line(module.price_run(dolphin_results())))
        finally:
            sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
