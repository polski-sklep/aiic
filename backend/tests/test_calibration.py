"""Tests for date-anchored calibration checkpoints and the backfill script.

Run inside the backend container (pytest is not in the image):

    docker compose exec backend python3 -m unittest tests.test_calibration -v

No test may touch CoinGecko or Postgres. All HTTP goes through
``app.knowledge.calibration._get_with_backoff``, which is patched, and the
backfill exercises an in-memory repository double.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.knowledge import calibration as cal  # noqa: E402
from scripts import backfill_checkpoints as bf  # noqa: E402


# Captured before the network guard below replaces it, so the "we still reuse the
# shared backoff helper" assertion can compare the real objects.
_ORIGINAL_BACKOFF = cal._get_with_backoff

_NET_GUARD = None


def setUpModule():
    """Hard-fail any test that tries to reach CoinGecko for real.

    Tests that legitimately exercise the HTTP layer patch _get_with_backoff (or
    fetch_price_on) themselves; this guard catches the ones that forget.
    """
    global _NET_GUARD

    def _no_network(*args, **kwargs):
        raise AssertionError(
            "test attempted a real CoinGecko request - patch _get_with_backoff "
            "or fetch_price_on"
        )

    _NET_GUARD = patch.object(cal, "_get_with_backoff", AsyncMock(side_effect=_no_network))
    _NET_GUARD.start()


def tearDownModule():
    if _NET_GUARD is not None:
        _NET_GUARD.stop()


def _response(payload, status_code=200):
    """A stand-in for httpx.Response, good enough for the code under test."""
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


class TestCoinGeckoDateFormat(unittest.TestCase):
    """CoinGecko's /history endpoint wants DD-MM-YYYY, not the ISO ordering."""

    def test_day_comes_first(self):
        self.assertEqual(cal.coingecko_date(date(2026, 7, 11)), "11-07-2026")
        self.assertEqual(cal.coingecko_date(date(2026, 7, 18)), "18-07-2026")

    def test_zero_padded(self):
        self.assertEqual(cal.coingecko_date(date(2026, 1, 5)), "05-01-2026")

    def test_ambiguous_date_is_not_month_first(self):
        # 7 November 2026, not 11 July 2026. This is the ordering bug this
        # format function exists to prevent.
        self.assertEqual(cal.coingecko_date(date(2026, 11, 7)), "07-11-2026")
        self.assertNotEqual(cal.coingecko_date(date(2026, 11, 7)), "11-07-2026")


class TestTargetDateDerivation(unittest.TestCase):
    """The target date comes from the record, never from now()."""

    def test_derives_entry_plus_horizon(self):
        entry = datetime(2026, 6, 18, 14, 3, tzinfo=timezone.utc)
        self.assertEqual(cal.resolve_target_date(entry, 30), date(2026, 7, 18))
        self.assertEqual(cal.resolve_target_date(entry, 90), date(2026, 9, 16))
        self.assertEqual(cal.resolve_target_date(entry, 180), date(2026, 12, 15))

    def test_aave_cohort_lands_on_11_july(self):
        entry = datetime(2026, 6, 11, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(cal.resolve_target_date(entry, 30), date(2026, 7, 11))

    def test_explicit_as_of_wins(self):
        entry = datetime(2026, 6, 18, tzinfo=timezone.utc)
        self.assertEqual(
            cal.resolve_target_date(entry, 30, as_of=date(2026, 8, 1)),
            date(2026, 8, 1),
        )

    def test_none_when_no_entry_and_no_as_of(self):
        self.assertIsNone(cal.resolve_target_date(None, 30))

    def test_accepts_naive_datetime(self):
        self.assertEqual(
            cal.resolve_target_date(datetime(2026, 6, 18, 14, 3), 30),
            date(2026, 7, 18),
        )

    def test_observation_timestamp_is_utc_midnight_of_target(self):
        ts = cal.observation_timestamp(date(2026, 7, 18))
        self.assertEqual(ts, datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc))


class TestHorizonValidation(unittest.TestCase):
    """Nothing unvalidated may reach the SQL f-string."""

    def test_accepts_only_30_90_180(self):
        for good in (30, 90, 180):
            self.assertTrue(cal._valid_horizon(good), good)
        for bad in (0, 1, 29, 31, 45, 60, 365, -30):
            self.assertFalse(cal._valid_horizon(bad), bad)

    def test_rejects_float_that_compares_equal(self):
        # 30.0 in (30, 90, 180) is True, but f"price_{30.0}d" is "price_30.0d".
        self.assertFalse(cal._valid_horizon(30.0))

    def test_rejects_bool(self):
        self.assertFalse(cal._valid_horizon(True))
        self.assertFalse(cal._valid_horizon(False))

    def test_rejects_strings(self):
        for bad in ("30", "30d", "30; DROP TABLE calibration_records--", ""):
            self.assertFalse(cal._valid_horizon(bad), bad)

    def test_column_map_holds_only_literal_names(self):
        self.assertEqual(sorted(cal.HORIZON_COLUMNS), [30, 90, 180])
        expected_30 = {
            "price": "price_30d",
            "checked_at": "checked_30d_at",
            "btc_price": "btc_price_30d",
            "return_pct": "return_30d_pct",
            "alpha_pct": "alpha_vs_btc_30d_pct",
        }
        self.assertEqual(cal.HORIZON_COLUMNS[30], expected_30)


class TestAlphaComputation(unittest.TestCase):
    """Alpha is a simple difference in percentage points, not a ratio."""

    def test_return_pct(self):
        return_pct, _ = cal.compute_returns(100.0, 90.0, None, None)
        self.assertAlmostEqual(return_pct, -10.0)

    def test_alpha_is_difference_not_ratio(self):
        # asset -10%, BTC -4%  ->  alpha -6 percentage points (not 2.5x)
        return_pct, alpha = cal.compute_returns(100.0, 90.0, 50000.0, 48000.0)
        self.assertAlmostEqual(return_pct, -10.0)
        self.assertAlmostEqual(alpha, -6.0)

    def test_positive_alpha_in_a_falling_market(self):
        # asset flat, BTC -20%  ->  alpha +20pp
        _, alpha = cal.compute_returns(1.0, 1.0, 100.0, 80.0)
        self.assertAlmostEqual(alpha, 20.0)

    def test_alpha_none_without_btc(self):
        _, alpha = cal.compute_returns(100.0, 90.0, 50000.0, None)
        self.assertIsNone(alpha)
        _, alpha = cal.compute_returns(100.0, 90.0, None, 48000.0)
        self.assertIsNone(alpha)

    def test_accepts_decimal_like_inputs(self):
        from decimal import Decimal

        return_pct, alpha = cal.compute_returns(
            Decimal("0.106058"), Decimal("0.09"), Decimal("63983"), Decimal("60000")
        )
        self.assertAlmostEqual(return_pct, -15.14, places=2)
        self.assertAlmostEqual(alpha, -8.92, places=2)

    def test_docstring_says_difference_not_ratio(self):
        doc = " ".join((cal.compute_returns.__doc__ or "").split())
        self.assertIn("simple arithmetic difference", doc)
        self.assertIn("It is NOT a ratio", doc)
        # update_checkpoint is the documented entry point; it must say so too.
        update_doc = " ".join((cal.update_checkpoint.__doc__ or "").split())
        self.assertIn("a difference, not a ratio", update_doc)


class TestFetchPriceOn(unittest.IsolatedAsyncioTestCase):
    """Historical price fetching, with CoinGecko fully mocked."""

    async def test_uses_history_endpoint_with_ddmmyyyy(self):
        mock_get = AsyncMock(
            return_value=_response(
                {"market_data": {"current_price": {"usd": 61.5}, "market_cap": {"usd": 9.2e8}}}
            )
        )
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("aave", date(2026, 7, 11))

        self.assertEqual(result.status, "found")
        self.assertEqual(result.price, 61.5)
        self.assertEqual(result.market_cap, 9.2e8)
        _client, path = mock_get.call_args.args
        self.assertEqual(path, "coins/aave/history")
        self.assertEqual(mock_get.call_args.kwargs["params"]["date"], "11-07-2026")

    async def test_sends_localization_false(self):
        # Cuts a large useless payload off every history response.
        mock_get = AsyncMock(
            return_value=_response({"market_data": {"current_price": {"usd": 1.0}}})
        )
        with patch.object(cal, "_get_with_backoff", mock_get):
            await cal.fetch_price_on("aave", date(2026, 7, 11))
        self.assertEqual(mock_get.call_args.kwargs["params"]["localization"], "false")

    async def test_absent_market_data_is_no_data_not_failure(self):
        # CoinGecko omits market_data entirely for dates before the coin existed.
        mock_get = AsyncMock(return_value=_response({"id": "plasma", "symbol": "xpl"}))
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("plasma", date(2020, 1, 1))
        self.assertEqual(result.status, "no_data")
        self.assertIsNone(result.price)
        self.assertFalse(result.failed)

    async def test_null_market_data_is_no_data(self):
        mock_get = AsyncMock(return_value=_response({"market_data": None}))
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("plasma", date(2020, 1, 1))
        self.assertEqual(result.status, "no_data")

    async def test_missing_usd_price_is_no_data(self):
        mock_get = AsyncMock(return_value=_response({"market_data": {"current_price": {}}}))
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("aave", date(2026, 7, 11))
        self.assertEqual(result.status, "no_data")

    async def test_http_rate_limit_exhaustion_is_a_failure(self):
        # _get_with_backoff returns None once the HTTP 429 retries are exhausted.
        mock_get = AsyncMock(return_value=None)
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("aave", date(2026, 7, 11))
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.failed)

    async def test_404_is_no_data(self):
        mock_get = AsyncMock(return_value=_response({}, status_code=404))
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("nosuchcoin", date(2026, 7, 11))
        self.assertEqual(result.status, "no_data")

    async def test_transport_error_is_a_failure(self):
        import httpx

        mock_get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
        with patch.object(cal, "_get_with_backoff", mock_get):
            result = await cal.fetch_price_on("aave", date(2026, 7, 11))
        self.assertEqual(result.status, "failed")
        self.assertIn("ConnectTimeout", result.detail)

    async def test_malformed_coin_id_never_reaches_the_url(self):
        mock_get = AsyncMock()
        with patch.object(cal, "_get_with_backoff", mock_get):
            for bad in ("../../etc/passwd", "aave/../bitcoin", "AAVE ", "a b"):
                result = await cal.fetch_price_on(bad, date(2026, 7, 11))
                self.assertEqual(result.status, "failed", bad)
        mock_get.assert_not_called()

    async def test_reuses_the_shared_backoff_helper(self):
        # Guards against someone reintroducing a bare httpx.get with no 429 handling.
        from app.tools import coingecko as cg

        self.assertIs(_ORIGINAL_BACKOFF, cg._get_with_backoff)
        self.assertEqual(cal.RETRY_DELAYS_SECONDS, (2, 4, 8, 16))


class TestBodyLevelRateLimit(unittest.IsolatedAsyncioTestCase):
    """The silent-corruption trap: HTTP 200 whose body is really a 429.

    CoinGecko's free tier answers an over-quota /history request with status 200
    and {"status": {"error_code": 429}} and no market_data key. Read naively
    that looks exactly like "the coin did not exist on that date", which on a
    backfill would record a rate limit as a genuine data gap.
    """

    RATE_LIMITED_BODY = {
        "status": {
            "error_code": 429,
            "error_message": "You've exceeded the Rate Limit. Please visit ...",
        }
    }

    def test_detector_recognises_the_body(self):
        self.assertTrue(cal.body_rate_limited(self.RATE_LIMITED_BODY))

    def test_detector_ignores_ordinary_bodies(self):
        self.assertFalse(cal.body_rate_limited({"market_data": {"current_price": {"usd": 1}}}))
        self.assertFalse(cal.body_rate_limited({"id": "plasma"}))
        self.assertFalse(cal.body_rate_limited({"status": {"error_code": 404}}))
        self.assertFalse(cal.body_rate_limited({"status": "ok"}))
        self.assertFalse(cal.body_rate_limited(None))
        self.assertFalse(cal.body_rate_limited([]))

    async def test_body_429_is_never_reported_as_no_data(self):
        mock_get = AsyncMock(return_value=_response(self.RATE_LIMITED_BODY))
        with patch.object(cal, "_get_with_backoff", mock_get):
            with patch.object(cal.asyncio, "sleep", AsyncMock()):
                result = await cal.fetch_price_on("aave", date(2026, 7, 11))

        self.assertEqual(result.status, "failed")
        self.assertNotEqual(result.status, "no_data", "a rate limit is not a data gap")
        self.assertIn("429", result.detail)

    async def test_body_429_is_retried_on_its_own_ladder(self):
        mock_get = AsyncMock(return_value=_response(self.RATE_LIMITED_BODY))
        sleeper = AsyncMock()
        with patch.object(cal, "_get_with_backoff", mock_get):
            with patch.object(cal.asyncio, "sleep", sleeper):
                await cal.fetch_price_on("aave", date(2026, 7, 11))

        # One initial attempt plus one per retry delay.
        self.assertEqual(mock_get.await_count, 1 + len(cal.HISTORY_RETRY_DELAYS_SECONDS))
        self.assertEqual(
            [c.args[0] for c in sleeper.await_args_list],
            list(cal.HISTORY_RETRY_DELAYS_SECONDS),
        )

    async def test_body_429_that_clears_on_retry_returns_the_price(self):
        mock_get = AsyncMock(
            side_effect=[
                _response(self.RATE_LIMITED_BODY),
                _response({"market_data": {"current_price": {"usd": 95.69}}}),
            ]
        )
        with patch.object(cal, "_get_with_backoff", mock_get):
            with patch.object(cal.asyncio, "sleep", AsyncMock()):
                result = await cal.fetch_price_on("aave", date(2026, 7, 11))
        self.assertEqual(result.status, "found")
        self.assertEqual(result.price, 95.69)

    async def test_history_ladder_is_longer_than_the_http_one(self):
        # The observed free-tier window is ~30s; (2,4,8,16) is not enough on its own.
        self.assertGreater(sum(cal.HISTORY_RETRY_DELAYS_SECONDS), 30)


class TestComputeCheckpoint(unittest.IsolatedAsyncioTestCase):
    """compute_checkpoint drives update_checkpoint; the DB read is mocked."""

    ENTRY = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)

    def _patch_record(self, row):
        session = AsyncMock()
        session.execute.return_value = SimpleNamespace(fetchone=lambda: row)
        ctx = AsyncMock()
        ctx.__aenter__.return_value = session
        ctx.__aexit__.return_value = False
        return patch.object(cal, "async_session", lambda: ctx)

    _UNSET = object()

    def _row(self, coin="plasma", entry_price=0.106058, btc=63983.0, entry=_UNSET):
        entry_at = self.ENTRY if entry is self._UNSET else entry
        return (coin, entry_price, btc, entry_at, "Plasma", "PASS")

    async def test_refuses_future_target_date(self):
        with self._patch_record(self._row()):
            fetch = AsyncMock()
            with patch.object(cal, "fetch_price_on", fetch):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001",
                    90,
                    today=date(2026, 8, 24),  # target would be 2026-09-16
                )
        self.assertIn("error", result)
        self.assertIn("in the future", result["error"])
        # Nothing was even fetched, let alone written.
        fetch.assert_not_called()

    async def test_refuses_explicit_future_as_of(self):
        with self._patch_record(self._row()):
            fetch = AsyncMock()
            with patch.object(cal, "fetch_price_on", fetch):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001",
                    30,
                    as_of=date(2026, 12, 25),
                    today=date(2026, 8, 24),
                )
        self.assertIn("error", result)
        self.assertIn("in the future", result["error"])
        fetch.assert_not_called()

    async def test_rejects_bad_horizon_before_touching_anything(self):
        for bad in (45, "30", 30.0, True):
            result = await cal.compute_checkpoint("irrelevant", bad)
            self.assertEqual(result, {"error": "horizon must be 30, 90, or 180"}, bad)

    async def test_fetches_asset_and_btc_at_the_same_date(self):
        prices = {
            ("plasma", date(2026, 7, 18)): 0.09,
            ("bitcoin", date(2026, 7, 18)): 60000.0,
        }

        async def fake_fetch(coin, day):
            if (coin, day) in prices:
                return cal.PriceLookup("found", price=prices[(coin, day)])
            return cal.PriceLookup("no_data")

        with self._patch_record(self._row()):
            with patch.object(cal, "fetch_price_on", side_effect=fake_fetch) as fetch:
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )

        dates_used = {call.args[1] for call in fetch.call_args_list}
        self.assertEqual(dates_used, {date(2026, 7, 18)}, "asset and BTC must share one date")
        self.assertEqual(result["target_date"], date(2026, 7, 18))
        self.assertEqual(
            result["observed_at"], datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)
        )
        self.assertTrue(result["is_reconstruction"])
        self.assertEqual(result["days_late"], 37)
        self.assertAlmostEqual(result["return_pct"], -15.14, places=2)
        self.assertAlmostEqual(result["alpha_vs_btc_pct"], -8.92, places=2)

    async def test_missing_market_data_is_an_error_not_a_crash(self):
        lookup = AsyncMock(return_value=cal.PriceLookup("no_data"))
        with self._patch_record(self._row()):
            with patch.object(cal, "fetch_price_on", lookup):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )
        self.assertIn("error", result)
        self.assertIn("no market_data", result["error"])
        self.assertFalse(result["fetch_failed"], "a genuine gap is not a fetch failure")

    async def test_failed_asset_fetch_is_flagged_as_a_failure(self):
        lookup = AsyncMock(return_value=cal.PriceLookup("failed", detail="429 persisted"))
        with self._patch_record(self._row()):
            with patch.object(cal, "fetch_price_on", lookup):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )
        self.assertTrue(result["fetch_failed"])
        self.assertIn("FAILED", result["error"])
        self.assertNotIn("no market_data", result["error"])

    async def test_failed_btc_fetch_refuses_to_write_an_unbenchmarked_row(self):
        async def fake_fetch(coin, day):
            if coin == "bitcoin":
                return cal.PriceLookup("failed", detail="429 persisted")
            return cal.PriceLookup("found", price=0.09)

        with self._patch_record(self._row()):
            with patch.object(cal, "fetch_price_on", side_effect=fake_fetch):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )
        self.assertTrue(result["fetch_failed"])
        self.assertIn("BTC benchmark fetch FAILED", result["error"])

    async def test_no_entry_date_and_no_as_of_is_an_error(self):
        fetch = AsyncMock()
        with self._patch_record(self._row(entry=None)):
            with patch.object(cal, "fetch_price_on", fetch):
                result = await cal.compute_checkpoint(
                    "0f6b9e4c-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )
        self.assertIn("error", result)
        self.assertIn("cannot determine target date", result["error"])
        fetch.assert_not_called()

    async def test_record_not_found(self):
        with self._patch_record(None):
            result = await cal.compute_checkpoint(
                "0f6b9e4c-0000-4000-8000-000000000001", 30
            )
        self.assertEqual(result, {"error": "record not found"})


class TestReconstructionNote(unittest.TestCase):
    def test_note_names_both_dates_and_says_it_is_a_reconstruction(self):
        note = cal.reconstruction_note(
            {
                "horizon_days": 30,
                "project_name": "Plasma",
                "coingecko_id": "plasma",
                "entry_captured_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
                "target_date": date(2026, 7, 18),
                "days_late": 37,
                "observed_price": 0.09,
                "btc_price_observed": 60000.0,
                "return_pct": -15.14,
                "alpha_vs_btc_pct": -8.92,
            },
            performed_on=date(2026, 8, 24),
        )
        self.assertIn("RECONSTRUCTED CHECKPOINT", note)
        self.assertIn("2026-07-18", note)  # true observation date
        self.assertIn("2026-08-24", note)  # date the reconstruction ran
        self.assertIn("2026-06-18", note)  # entry date
        self.assertIn("NOT captured on the day", note)
        self.assertIn("not a ratio", note)


# --- backfill script ---------------------------------------------------------


class FakeRepo:
    """In-memory stand-in for PostgresRepo, so idempotency can be tested for real."""

    def __init__(self, records, has_outcome_notes=True):
        self.records = records
        self.has_outcome_notes = has_outcome_notes
        self.checkpoint_writes = []
        self.note_appends = []

    async def column_exists(self, column):
        return self.has_outcome_notes if column == "outcome_notes" else True

    async def fetch_records(self):
        # Return copies, the way a fresh SELECT would.
        return [bf.Record(**vars(r)) for r in self.records]

    async def write_checkpoint(self, record_id, horizon_days, price, btc_price,
                               return_pct, alpha_pct, checked_at, note):
        self.checkpoint_writes.append(
            {
                "record_id": record_id,
                "horizon_days": horizon_days,
                "price": price,
                "btc_price": btc_price,
                "return_pct": return_pct,
                "alpha_pct": alpha_pct,
                "checked_at": checked_at,
                "note": note,
            }
        )
        for r in self.records:
            if r.id == record_id:
                r.existing_price = price
                r.outcome_notes = ((r.outcome_notes or "") + "\n\n" + note).strip()

    async def append_note(self, record_id, note):
        self.note_appends.append({"record_id": record_id, "note": note})
        for r in self.records:
            if r.id == record_id:
                r.outcome_notes = ((r.outcome_notes or "") + "\n\n" + note).strip()


def _record(rid, name, coin, rec, entry_price, btc, entry_day, ticker="X"):
    return bf.Record(
        id=rid,
        project_name=name,
        ticker=ticker,
        coingecko_id=coin,
        recommendation=rec,
        entry_price_usd=entry_price,
        btc_price_at_entry=btc,
        entry_captured_at=datetime.combine(entry_day, datetime.min.time(), tzinfo=timezone.utc),
        existing_price=None,
        outcome_notes=None,
    )


def _ledger():
    """A stand-in for the eight live rows in docs/CONTRACTS.md §2.6."""
    jun11, jun18 = date(2026, 6, 11), date(2026, 6, 18)
    return [
        _record("id-aave-bad", "Aave", "aave", "INSUFFICIENT_DATA", None, 62779.0, jun11, "AAVE"),
        _record("id-aave", "Aave", "aave", "PASS", 63.09, 62964.0, jun11, "AAVE"),
        _record("id-plasma-bad", "Plasma", "plasma", "INSUFFICIENT_DATA", 0.108772, 64009.0, jun18, "XPL"),
        _record("id-plasma", "Plasma", "plasma", "PASS", 0.106058, 63983.0, jun18, "XPL"),
        _record("id-geodnet", "GEODNET", "geodnet", "WATCH", 0.216691, 64090.0, jun18, "GEOD"),
        _record("id-ethena", "Ethena", "ethena", "WATCH", 0.094421, 63960.0, jun18, "ENA"),
        _record("id-morpho", "Morpho", "morpho", "WATCH", 1.99, 63964.0, jun18, "MORPHO"),
        _record("id-pendle", "Pendle", "pendle", "WATCH", 1.43, 63889.0, jun18, "PENDLE"),
    ]


def _fake_price_source():
    """Deterministic prices for every (coin, date) the backfill will ask for."""
    base = {
        "aave": 63.09, "plasma": 0.106058, "geodnet": 0.216691,
        "ethena": 0.094421, "morpho": 1.99, "pendle": 1.43, "bitcoin": 64000.0,
    }
    factor = {date(2026, 7, 11): 0.9, date(2026, 7, 18): 0.85, date(2026, 8, 24): 0.7}
    calls = []

    async def fetch(coin, day):
        calls.append((coin, day))
        if coin not in base or day not in factor:
            return cal.PriceLookup("no_data")
        return cal.PriceLookup("found", price=round(base[coin] * factor[day], 8))

    return fetch, calls


class TestBackfillDryRun(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_writes_nothing(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        rc = await bf.run(
            repo, commit=False, force=False, performed_on=date(2026, 8, 24),
            cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out,
        )
        self.assertEqual(rc, bf.EXIT_OK)
        self.assertEqual(repo.checkpoint_writes, [])
        self.assertEqual(repo.note_appends, [])
        self.assertIn("DRY RUN", out.getvalue())

    async def test_dry_run_is_the_default_in_the_cli(self):
        args = bf.build_parser().parse_args([])
        self.assertFalse(args.commit)
        self.assertFalse(args.force)

    async def test_dry_run_prints_every_column_it_would_write(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        await bf.run(repo, commit=False, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        text = out.getvalue()
        for column in ("price_30d", "btc_price_30d", "return_30d_pct",
                       "alpha_vs_btc_30d_pct", "checked_30d_at"):
            self.assertIn(column, text)
        self.assertIn("TRUE observation date, not now()", text)

    async def test_insufficient_data_rows_are_skipped_and_said_so(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        await bf.run(repo, commit=False, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        text = out.getvalue()
        self.assertIn("records skipped entirely: 2", text)
        self.assertIn("failed committee", text)


class TestBackfillCommit(unittest.IsolatedAsyncioTestCase):
    async def test_writes_six_checkpoints_at_the_right_dates(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, calls = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())

        self.assertEqual(len(repo.checkpoint_writes), 6)
        by_id = {w["record_id"]: w for w in repo.checkpoint_writes}
        self.assertEqual(
            by_id["id-aave"]["checked_at"],
            datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc),
        )
        for rid in ("id-plasma", "id-geodnet", "id-ethena", "id-morpho", "id-pendle"):
            self.assertEqual(
                by_id[rid]["checked_at"],
                datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
                rid,
            )
        self.assertTrue(all(w["horizon_days"] == 30 for w in repo.checkpoint_writes))

    async def test_every_write_is_stamped_as_a_reconstruction(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        for w in repo.checkpoint_writes:
            self.assertIn(bf.RECONSTRUCTION_MARKER, w["note"])
            self.assertIn("2026-08-24", w["note"])  # date the backfill ran
            self.assertIn("NOT captured on the day", w["note"])

    async def test_mark_to_market_is_prose_only(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())

        self.assertEqual(len(repo.note_appends), 6)
        for a in repo.note_appends:
            self.assertIn(bf.MARK_MARKER, a["note"])
            self.assertIn("Deliberately NOT written to price_30d", a["note"])
        # The mark never lands in a dated column: the only dated writes are the
        # 30d checkpoints, all at July dates, never at the 24 August mark date.
        for w in repo.checkpoint_writes:
            self.assertNotEqual(w["checked_at"].date(), date(2026, 8, 24))

    async def test_67_day_elapsed_appears_for_the_june_18_cohort(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        cohort = [a for a in repo.note_appends if a["record_id"] == "id-plasma"]
        self.assertEqual(len(cohort), 1)
        self.assertIn("67-day mark-to-market", cohort[0]["note"])
        aave = [a for a in repo.note_appends if a["record_id"] == "id-aave"]
        self.assertIn("74-day mark-to-market", aave[0]["note"])

    async def test_price_cache_keeps_coingecko_calls_low(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, calls = _fake_price_source()
        cache = bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None)
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=cache, out=io.StringIO())
        # 6 assets x 2 dates (30d target + mark date) + BTC on 3 distinct dates.
        self.assertEqual(cache.calls, 15)
        self.assertEqual(len(calls), 15)
        self.assertEqual(len(set(calls)), 15, "no (coin, date) pair fetched twice")


class TestBackfillIdempotency(unittest.IsolatedAsyncioTestCase):
    async def test_second_run_writes_nothing(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()

        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        first_checkpoints = len(repo.checkpoint_writes)
        first_notes = len(repo.note_appends)
        self.assertEqual((first_checkpoints, first_notes), (6, 6))

        out = io.StringIO()
        rc = await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 25),
                          cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)

        self.assertEqual(rc, bf.EXIT_OK)
        self.assertEqual(len(repo.checkpoint_writes), first_checkpoints, "double-wrote checkpoints")
        self.assertEqual(len(repo.note_appends), first_notes, "double-wrote notes")
        self.assertIn("Ledger already up to date", out.getvalue())

    async def test_second_run_explains_why_it_skipped(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        out = io.StringIO()
        await bf.run(repo, commit=False, force=False, performed_on=date(2026, 8, 25),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        text = out.getvalue()
        self.assertIn("use --force to overwrite", text)
        self.assertIn("already contains a mark-to-market", text)

    async def test_force_overwrites(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        await bf.run(repo, commit=True, force=True, performed_on=date(2026, 8, 25),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        self.assertEqual(len(repo.checkpoint_writes), 12)
        self.assertEqual(len(repo.note_appends), 12)

    async def test_a_record_with_an_existing_price_is_left_alone(self):
        import io

        ledger = _ledger()
        for r in ledger:
            if r.id == "id-morpho":
                r.existing_price = 1.55
        repo = FakeRepo(ledger)
        fetch, _ = _fake_price_source()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=io.StringIO())
        written = {w["record_id"] for w in repo.checkpoint_writes}
        self.assertNotIn("id-morpho", written)
        self.assertEqual(len(written), 5)


class TestBackfillGuards(unittest.IsolatedAsyncioTestCase):
    async def test_missing_outcome_notes_column_fails_clearly(self):
        import io

        repo = FakeRepo(_ledger(), has_outcome_notes=False)
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        rc = await bf.run(repo, commit=True, force=True, performed_on=date(2026, 8, 24),
                          cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)

        self.assertEqual(rc, bf.EXIT_MISSING_COLUMN)
        self.assertEqual(repo.checkpoint_writes, [])
        self.assertEqual(repo.note_appends, [])
        text = out.getvalue()
        self.assertIn("outcome_notes does not exist", text)
        self.assertIn("agent/persistence", text)

    async def test_future_target_date_is_refused_in_the_backfill_too(self):
        import io

        repo = FakeRepo(_ledger())
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        # Pretend today is 25 June: every 30d target is still in the future.
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 6, 25),
                     mark_as_of=date(2026, 6, 25), cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        self.assertEqual(repo.checkpoint_writes, [])
        self.assertIn("is in the future", out.getvalue())

    async def test_records_missing_a_coingecko_id_are_skipped(self):
        import io

        ledger = _ledger()
        for r in ledger:
            if r.id == "id-pendle":
                r.coingecko_id = None
        repo = FakeRepo(ledger)
        fetch, _ = _fake_price_source()
        out = io.StringIO()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        self.assertNotIn("id-pendle", {w["record_id"] for w in repo.checkpoint_writes})
        self.assertIn("no coingecko_id", out.getvalue())

    async def test_unavailable_history_is_reported_not_written(self):
        import io

        async def fetch(coin, day):
            if coin == "geodnet":
                return cal.PriceLookup("no_data")
            return cal.PriceLookup("found", price=1.0)

        repo = FakeRepo(_ledger())
        out = io.StringIO()
        await bf.run(repo, commit=True, force=False, performed_on=date(2026, 8, 24),
                     cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out)
        self.assertNotIn("id-geodnet", {w["record_id"] for w in repo.checkpoint_writes})
        self.assertIn("no market_data for 'geodnet'", out.getvalue())

    async def test_cli_rejects_an_invalid_horizon(self):
        with self.assertRaises(SystemExit):
            bf.build_parser().parse_args(["--horizon", "45"])

    async def test_describe_database_hides_the_password(self):
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://u:hunter2@db:5432/committee"}):
            described = bf._describe_database()
        self.assertNotIn("hunter2", described)
        self.assertEqual(described, "db:5432/committee")


class TestBackfillAbortsOnFetchFailure(unittest.IsolatedAsyncioTestCase):
    """A failed fetch must abort the run, not produce a partial ledger."""

    async def test_failure_midway_writes_nothing_at_all(self):
        import io

        async def fetch(coin, day):
            # Everything works until Morpho, which is rate limited.
            if coin == "morpho":
                return cal.PriceLookup("failed", detail="body-level 429 persisted")
            return cal.PriceLookup("found", price=1.0)

        repo = FakeRepo(_ledger())
        out = io.StringIO()
        rc = await bf.run(
            repo, commit=True, force=False, performed_on=date(2026, 8, 24),
            cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out,
        )

        self.assertEqual(rc, bf.EXIT_FAILED)
        self.assertEqual(repo.checkpoint_writes, [], "partial write after a failed fetch")
        self.assertEqual(repo.note_appends, [], "partial write after a failed fetch")
        text = out.getvalue()
        self.assertIn("ABORTED", text)
        self.assertIn("NOTHING WAS WRITTEN", text)

    async def test_failure_is_not_reported_as_a_data_gap(self):
        import io

        async def fetch(coin, day):
            return cal.PriceLookup("failed", detail="body-level 429 persisted")

        out = io.StringIO()
        await bf.run(
            FakeRepo(_ledger()), commit=True, force=False, performed_on=date(2026, 8, 24),
            cache=bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None), out=out,
        )
        text = out.getvalue()
        self.assertNotIn("no market_data", text)
        self.assertIn("429", text)

    async def test_cache_never_stores_a_failure(self):
        cache = bf.PriceCache(
            AsyncMock(return_value=cal.PriceLookup("failed", detail="429")),
            min_interval_seconds=0,
            cache_file=None,
        )
        with self.assertRaises(bf.FetchFailed):
            await cache.price_on("aave", date(2026, 7, 11))
        # A second attempt must retry, not serve a cached failure.
        with self.assertRaises(bf.FetchFailed):
            await cache.price_on("aave", date(2026, 7, 11))
        self.assertEqual(cache.calls, 2)
        self.assertEqual(cache.cache_hits, 0)


class TestPriceCache(unittest.IsolatedAsyncioTestCase):
    async def test_same_coin_and_date_fetched_once(self):
        fetch, calls = _fake_price_source()
        cache = bf.PriceCache(fetch, min_interval_seconds=0, cache_file=None)
        for _ in range(4):
            await cache.price_on("bitcoin", date(2026, 7, 18))
        self.assertEqual(cache.calls, 1)
        self.assertEqual(cache.cache_hits, 3)
        self.assertEqual(len(calls), 1)

    async def test_disk_cache_survives_into_a_second_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "prices.json")

            fetch1, calls1 = _fake_price_source()
            cache1 = bf.PriceCache(fetch1, min_interval_seconds=0, cache_file=path)
            await cache1.price_on("aave", date(2026, 7, 11))
            await cache1.price_on("bitcoin", date(2026, 7, 11))
            self.assertEqual(len(calls1), 2)

            # A fresh cache, as the --commit run would build.
            fetch2, calls2 = _fake_price_source()
            cache2 = bf.PriceCache(fetch2, min_interval_seconds=0, cache_file=path)
            price = await cache2.price_on("aave", date(2026, 7, 11))
            await cache2.price_on("bitcoin", date(2026, 7, 11))

            self.assertEqual(calls2, [], "--commit re-fetched what the dry run already had")
            self.assertEqual(cache2.calls, 0)
            self.assertEqual(cache2.cache_hits, 2)
            self.assertEqual(price, round(63.09 * 0.9, 8))

    async def test_disk_cache_stores_no_data_but_not_failures(self):
        import json as _json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"

            async def fetch(coin, day):
                if coin == "plasma":
                    return cal.PriceLookup("no_data", detail="predates listing")
                return cal.PriceLookup("failed", detail="429")

            cache = bf.PriceCache(fetch, min_interval_seconds=0, cache_file=str(path))
            await cache.price_on("plasma", date(2020, 1, 1))
            with self.assertRaises(bf.FetchFailed):
                await cache.price_on("aave", date(2020, 1, 1))

            stored = _json.loads(path.read_text())
            self.assertIn("plasma@2020-01-01", stored)
            self.assertNotIn("aave@2020-01-01", stored)
            self.assertEqual(stored["plasma@2020-01-01"]["status"], "no_data")

    async def test_unreadable_cache_file_is_ignored_not_fatal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.json"
            path.write_text("this is not json{{{")
            fetch, _ = _fake_price_source()
            cache = bf.PriceCache(fetch, min_interval_seconds=0, cache_file=str(path))
            self.assertEqual(await cache.price_on("aave", date(2026, 7, 11)), round(63.09 * 0.9, 8))

    async def test_pacing_waits_between_calls(self):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        fetch, _ = _fake_price_source()
        cache = bf.PriceCache(fetch, min_interval_seconds=20, cache_file=None)
        with patch.object(bf.asyncio, "sleep", fake_sleep):
            await cache.price_on("aave", date(2026, 7, 11))
            await cache.price_on("bitcoin", date(2026, 7, 11))
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 19)

    async def test_default_pacing_is_conservative(self):
        # The orchestrator observed a 429 on the 4th call with 8s spacing.
        self.assertGreaterEqual(bf.DEFAULT_MIN_INTERVAL_SECONDS, 15)


class TestPriceLookupInvariant(unittest.TestCase):
    def test_found_without_a_price_is_rejected_at_construction(self):
        # Otherwise it flows into float() and raises TypeError frames away.
        with self.assertRaises(ValueError):
            cal.PriceLookup("found")
        with self.assertRaises(ValueError):
            cal.PriceLookup("found", price=None)

    def test_no_data_and_failed_may_have_no_price(self):
        self.assertIsNone(cal.PriceLookup("no_data").price)
        self.assertIsNone(cal.PriceLookup("failed", detail="429").price)

    def test_found_with_a_price_is_fine(self):
        self.assertEqual(cal.PriceLookup("found", price=0.0).price, 0.0)


class TestNullEntryPrice(unittest.IsolatedAsyncioTestCase):
    """The 11 June Aave INSUFFICIENT_DATA row has a NULL entry_price_usd.

    The HTTP endpoint can be pointed at it, so this path is reachable in
    production, not merely theoretical.
    """

    def _patch_record(self, row):
        session = AsyncMock()
        session.execute.return_value = SimpleNamespace(fetchone=lambda: row)
        ctx = AsyncMock()
        ctx.__aenter__.return_value = session
        ctx.__aexit__.return_value = False
        return patch.object(cal, "async_session", lambda: ctx)

    async def test_null_entry_price_is_a_clean_error_not_a_typeerror(self):
        row = ("aave", None, 62779.0, datetime(2026, 6, 11, tzinfo=timezone.utc),
               "Aave", "INSUFFICIENT_DATA")
        fetch = AsyncMock()
        with self._patch_record(row):
            with patch.object(cal, "fetch_price_on", fetch):
                result = await cal.compute_checkpoint(
                    "89b57672-0000-4000-8000-000000000001", 30, today=date(2026, 8, 24)
                )
        self.assertEqual(result, {"error": "record has no entry_price_usd"})
        # And it bails before spending a CoinGecko call.
        fetch.assert_not_called()


class TestRecordSignposts(unittest.IsolatedAsyncioTestCase):
    """signposts / review_date land via a separate function because
    record_calibration's signature is frozen (CONTRACTS 3.1)."""

    def _patch_session(self, *, column_present=True, rowcount=1):
        session = AsyncMock()
        self.executed = []

        async def execute(statement, params=None):
            self.executed.append((str(statement), params))
            if "information_schema" in str(statement):
                return SimpleNamespace(fetchone=lambda: (1,) if column_present else None)
            return SimpleNamespace(rowcount=rowcount)

        session.execute = execute
        ctx = AsyncMock()
        ctx.__aenter__.return_value = session
        ctx.__aexit__.return_value = False
        return patch.object(cal, "async_session", lambda: ctx)

    async def test_writes_signposts_and_review_date(self):
        with self._patch_session():
            ok = await cal.record_signposts(
                "815e976e-0000-4000-8000-000000000005",
                ["RTK subscriber growth stalls", "unlock cliff announced"],
                "2026-09-16",
            )
        self.assertTrue(ok)
        update = [e for e in self.executed if "UPDATE" in e[0]][0]
        self.assertEqual(
            update[1]["signposts"],
            '["RTK subscriber growth stalls", "unlock cliff announced"]',
        )
        self.assertEqual(update[1]["review_date"], date(2026, 9, 16))

    async def test_uses_cast_not_the_postfix_colon_colon(self):
        # `:param::jsonb` collides with SQLAlchemy's bind syntax and raises
        # PostgresSyntaxError - the same trap already fixed for ::vector
        # (PROJECT_DECISIONS D2). Verified against live Postgres as well.
        with self._patch_session():
            await cal.record_signposts("815e976e-0000-4000-8000-000000000005", ["x"], None)
        sql = [e for e in self.executed if "UPDATE" in e[0]][0][0]
        self.assertIn("CAST(:signposts AS jsonb)", sql)
        self.assertNotIn("::jsonb", sql)

    async def test_no_op_when_both_arguments_are_none(self):
        session_used = False

        def _fail():
            nonlocal session_used
            session_used = True
            raise AssertionError("should not open a session")

        with patch.object(cal, "async_session", _fail):
            self.assertFalse(await cal.record_signposts("some-id", None, None))
        self.assertFalse(session_used)

    async def test_no_op_when_migration_0003_has_not_run(self):
        with self._patch_session(column_present=False):
            ok = await cal.record_signposts(
                "815e976e-0000-4000-8000-000000000005", ["x"], "2026-09-16"
            )
        self.assertFalse(ok)
        self.assertEqual([e for e in self.executed if "UPDATE" in e[0]], [])

    async def test_unparseable_review_date_is_dropped_not_fatal(self):
        with self._patch_session():
            ok = await cal.record_signposts(
                "815e976e-0000-4000-8000-000000000005", ["x"], "not-a-date"
            )
        self.assertTrue(ok, "signposts should still land")
        update = [e for e in self.executed if "UPDATE" in e[0]][0]
        self.assertIsNone(update[1]["review_date"])

    async def test_only_a_review_date_is_enough(self):
        with self._patch_session():
            ok = await cal.record_signposts(
                "815e976e-0000-4000-8000-000000000005", None, "2026-09-16"
            )
        self.assertTrue(ok)
        update = [e for e in self.executed if "UPDATE" in e[0]][0]
        self.assertIsNone(update[1]["signposts"])
        self.assertEqual(update[1]["review_date"], date(2026, 9, 16))

    async def test_empty_signpost_list_is_stored_as_an_answer(self):
        # "the Chair named none" is information; None means "not supplied".
        with self._patch_session():
            ok = await cal.record_signposts(
                "815e976e-0000-4000-8000-000000000005", [], None
            )
        self.assertTrue(ok)
        update = [e for e in self.executed if "UPDATE" in e[0]][0]
        self.assertEqual(update[1]["signposts"], "[]")

    async def test_unknown_record_returns_false(self):
        with self._patch_session(rowcount=0):
            ok = await cal.record_signposts(
                "00000000-0000-4000-8000-000000000999", ["x"], "2026-09-16"
            )
        self.assertFalse(ok)

    async def test_database_failure_is_non_fatal(self):
        # Calibration capture must never take the evaluation pipeline down.
        def _boom():
            raise RuntimeError("connection refused")

        with patch.object(cal, "async_session", _boom):
            self.assertFalse(
                await cal.record_signposts("some-id", ["x"], "2026-09-16")
            )

    async def test_record_calibration_signature_is_unchanged(self):
        import inspect

        params = list(inspect.signature(cal.record_calibration).parameters)
        self.assertEqual(
            params,
            ["evaluation_id", "project_name", "ticker", "coingecko_id", "category",
             "recommendation", "overall_score", "chair_confidence", "vetoed"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
