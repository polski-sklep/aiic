"""The structural gate: a hard blocker that runs before any LLM call.

run_structural_gate decides whether the committee spends anything on a project.
Two questions matter: what does it let through that it should block, and what
does it die on.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.agents.guardrails import run_structural_gate


def days_ago(n: int) -> str:
    """CoinGecko's genesis_date shape: a bare calendar date, no timezone."""
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def days_ago_tz(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class GatePassesWhatItShouldTest(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_project_passes_with_no_blocking_failures(self):
        result = await run_structural_gate(
            {
                "project_name": "Aave",
                "coingecko_id": "aave",
                "category": "Lending",
                "_price_data": {"market_cap": 950_000_000, "volume_24h": 120_000_000},
                "_token_data": {
                    "market_cap_usd": 950_000_000,
                    "fully_diluted_valuation": 1_000_000_000,
                    "genesis_date": days_ago_tz(2000),
                },
            }
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_failures, [])

    async def test_absent_data_produces_warnings_not_blocks(self):
        """A thin-data project must not be blocked, only flagged."""
        result = await run_structural_gate({"coingecko_id": "", "_price_data": {}, "_token_data": {}})
        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_failures, [])
        self.assertEqual(len(result.warnings), 3)


class GateBlocksWhatItShouldTest(unittest.IsolatedAsyncioTestCase):
    async def test_market_cap_below_one_million_blocks(self):
        result = await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {"market_cap": 999_999}, "_token_data": {}}
        )
        self.assertFalse(result.passed)
        self.assertIn("below $1M", result.blocking_failures[0])

    async def test_extreme_fdv_ratio_blocks_and_high_ratio_only_warns(self):
        blocked = await run_structural_gate(
            {
                "coingecko_id": "x",
                "_price_data": {},
                "_token_data": {"market_cap_usd": 10_000_000, "fully_diluted_valuation": 600_000_000},
            }
        )
        self.assertFalse(blocked.passed)

        warned = await run_structural_gate(
            {
                "coingecko_id": "x",
                "_price_data": {},
                "_token_data": {"market_cap_usd": 10_000_000, "fully_diluted_valuation": 150_000_000},
            }
        )
        self.assertTrue(warned.passed)
        self.assertTrue(any("dilution" in w for w in warned.warnings))

    async def test_excluded_category_blocks(self):
        for category in ("meme", "Memecoin", "GAMBLING", "Adult Entertainment"):
            result = await run_structural_gate(
                {"coingecko_id": "x", "category": category, "_price_data": {}, "_token_data": {}}
            )
            self.assertFalse(result.passed, category)

    async def test_low_volume_warns_but_does_not_block(self):
        """Deliberate: illiquidity is a risk input, not a disqualifier."""
        result = await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {"market_cap": 5_000_000, "volume_24h": 500}, "_token_data": {}}
        )
        self.assertTrue(result.passed)
        self.assertFalse(result.checks["min_volume"]["passed"])


class GateDefectsTest(unittest.IsolatedAsyncioTestCase):
    @unittest.expectedFailure
    async def test_QA_014_min_age_gate_must_fire_on_real_coingecko_dates(self):
        """QA-014 (HIGH): the 90-day minimum-age gate is dead against live data.

        CoinGecko returns genesis_date as a bare "YYYY-MM-DD". guardrails.py
        parses it with datetime.fromisoformat, which yields a *naive* datetime,
        then subtracts it from an aware datetime.now(timezone.utc). That raises
        TypeError, which the except clause catches, and the check is recorded as
        {"passed": True, "reason": "Could not parse genesis date"}.

        Only a genesis_date that already carries a timezone ever blocks -- and
        tools/coingecko.py::get_token_info passes CoinGecko's value through
        untouched. A three-week-old token clears the gate.
        """
        result = await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {}, "_token_data": {"genesis_date": days_ago(23)}}
        )
        self.assertFalse(result.passed, "a 23-day-old project passed the 90-day minimum")

    async def test_QA_014_control_a_tz_aware_genesis_date_does_block(self):
        """Control for QA-014: the logic is right, only the date shape defeats it."""
        result = await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {}, "_token_data": {"genesis_date": days_ago_tz(23)}}
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.checks["min_age"]["age_days"], 23)

    @unittest.expectedFailure
    async def test_QA_015_null_prefetch_blocks_must_not_crash_the_gate(self):
        """QA-015 (HIGH): ``.get(key, {})`` does not defend against an explicit None.

        Protocol Resolution setting _price_data to None on a failed lookup -- the
        obvious way to express "we tried and got nothing" -- takes the gate down
        with an AttributeError before any check runs. The gate is the step that
        is supposed to fail *gracefully* on thin data.
        """
        result = await run_structural_gate(
            {"coingecko_id": "aave", "_price_data": None, "_token_data": None}
        )
        self.assertTrue(result.passed)

    @unittest.expectedFailure
    async def test_QA_016_zero_market_cap_must_block(self):
        """QA-016 (MED): ``if market_cap and market_cap < 1_000_000`` -- 0 is falsy.

        A market cap of exactly 0 (delisted, pre-launch, or a CoinGecko gap
        reported as 0 rather than null) falls through to the else branch and is
        recorded as "No market cap data - skipping check". Zero is not missing
        data; it is the most extreme possible failure of the $1M minimum.
        """
        result = await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {"market_cap": 0}, "_token_data": {}}
        )
        self.assertFalse(result.passed)

    @unittest.expectedFailure
    async def test_QA_017_string_market_cap_must_not_crash(self):
        """QA-017 (MED): no type coercion before the numeric comparison."""
        await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {"market_cap": "500000"}, "_token_data": {}}
        )

    @unittest.expectedFailure
    async def test_QA_017_list_category_must_not_crash(self):
        """QA-017 (MED): CoinGecko's field is ``categories``, a list.

        ``(project_info.get("category", "") or "").lower()`` -- the ``or ""``
        guards None but a non-empty list is truthy and has no .lower().
        Passing CoinGecko's own categories list straight in kills the gate.
        """
        await run_structural_gate(
            {"coingecko_id": "x", "category": ["Meme", "Solana Ecosystem"], "_price_data": {}, "_token_data": {}}
        )

    @unittest.expectedFailure
    async def test_QA_017_non_string_genesis_must_not_crash(self):
        """QA-017 (MED): ``genesis.replace(...)`` raises AttributeError on an int.

        The except clause catches ValueError and TypeError but not AttributeError,
        so a unix-timestamp genesis date escapes the guard entirely.
        """
        await run_structural_gate(
            {"coingecko_id": "x", "_price_data": {}, "_token_data": {"genesis_date": 1600000000}}
        )

    @unittest.expectedFailure
    async def test_QA_018_mandate_exclusion_must_consider_coingecko_categories(self):
        """QA-018 (MED): the exclusion reads one caller-supplied free-text field.

        _token_data carries CoinGecko's real ``categories``, which is where the
        truth about a memecoin lives. The gate ignores it. Any caller that does
        not hand-populate ``category`` -- the API accepts the field as optional --
        bypasses the mandate exclusion completely.
        """
        result = await run_structural_gate(
            {
                "coingecko_id": "dogwifhat",
                "_price_data": {"market_cap": 900_000_000},
                "_token_data": {"categories": ["Meme", "Solana Ecosystem"]},
            }
        )
        self.assertFalse(result.passed, "a memecoin cleared the mandate exclusion")

    @unittest.expectedFailure
    async def test_QA_015_missing_project_info_must_not_crash(self):
        """QA-015 (HIGH), same root: None instead of a dict."""
        await run_structural_gate(None)


if __name__ == "__main__":
    unittest.main()
