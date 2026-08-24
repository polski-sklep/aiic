"""Tool argument handling, backoff, and error shapes -- all HTTP mocked.

Every test here runs inside ``mock_http``, which patches httpx.AsyncClient onto
a MockTransport *and* blocks socket.connect / create_connection / getaddrinfo.
If any of these tests ever reach a real API they fail with NetworkAccessError
rather than quietly hitting CoinGecko.

The central question this file asks is the one from the QA brief: can an agent
reading a tool result tell "there is no data" apart from "the call failed"?
"""
from __future__ import annotations

import unittest

import httpx

from tests._support import instant_sleep, json_response, mock_http, settings_override


class CoinGeckoBackoffTest(unittest.IsolatedAsyncioTestCase):
    """Handoff section 9.5 records "CoinGecko 429 storms" as fixed by backoff.

    Testing for the presence of the fix would be grepping for RETRY_DELAYS_SECONDS.
    These test the behaviour instead.
    """

    async def test_sustained_429_exhausts_the_documented_delays_and_reports_rate_limiting(self):
        from app.tools import coingecko

        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(str(request.url))
            return httpx.Response(429, json={"status": {"error_message": "rate limited"}})

        with mock_http(handler), instant_sleep() as delays:
            result = await coingecko.get_price({"coin_id": "aave"})

        self.assertEqual(len(attempts), 5, "one initial attempt plus four retries")
        self.assertEqual(delays, [2, 4, 8, 16])
        self.assertIn("rate limit", result["error"])

    async def test_a_429_that_clears_returns_data_without_burning_the_rest_of_the_budget(self):
        from app.tools import coingecko

        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] < 3:
                return httpx.Response(429, json={})
            return httpx.Response(
                200,
                json={"aave": {"usd": 63.09, "usd_market_cap": 9.5e8, "usd_24h_vol": 1e8, "usd_24h_change": 1.2}},
            )

        with mock_http(handler), instant_sleep() as delays:
            result = await coingecko.get_price({"coin_id": "aave"})

        self.assertEqual(state["n"], 3)
        self.assertEqual(delays, [2, 4], "kept retrying after success")
        self.assertEqual(result["price"], 63.09)

    async def test_non_429_errors_are_not_retried(self):
        from app.tools import coingecko

        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(500, json={})

        with mock_http(handler), instant_sleep():
            result = await coingecko.get_price({"coin_id": "aave"})

        self.assertEqual(len(attempts), 1)
        self.assertIn("500", result["error"])

    async def test_the_api_key_header_is_sent_only_when_configured(self):
        from app.tools import coingecko

        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.headers))
            return httpx.Response(200, json={"aave": {"usd": 1}})

        with settings_override(coingecko_api_key=""), mock_http(handler):
            await coingecko.get_price({"coin_id": "aave"})
        self.assertNotIn("x-cg-demo-api-key", seen[-1])

        with settings_override(coingecko_api_key="demo-key"), mock_http(handler):
            await coingecko.get_price({"coin_id": "aave"})
        self.assertEqual(seen[-1]["x-cg-demo-api-key"], "demo-key")


class CoinGeckoBodyLevelRateLimitTest(unittest.IsolatedAsyncioTestCase):
    """HTTP 200 whose body is really a 429.

    ``agent/calibration`` found this against /coins/{id}/history and handles it
    in ``app.knowledge.calibration.body_rate_limited``. The agent-facing tools in
    ``app/tools/coingecko.py`` share the same free-tier quota and the same
    ``_get_with_backoff`` pattern but have no equivalent check, so the trap is
    still open on the path the committee actually runs on.

    ``_get_with_backoff`` only inspects ``response.status_code``, so a 200 ends
    the retry loop immediately and the body is parsed as data.
    """

    RATE_LIMITED_BODY = {
        "status": {
            "error_code": 429,
            "error_message": "You've exceeded the Rate Limit. Please visit ...",
        }
    }

    def test_the_detector_exists_and_recognises_the_body(self):
        """Cross-check: calibration's detector is correct and reusable."""
        from app.knowledge.calibration import body_rate_limited

        self.assertTrue(body_rate_limited(self.RATE_LIMITED_BODY))
        self.assertFalse(body_rate_limited({"aave": {"usd": 63.0}}))

    async def test_QA_042_body_level_429_reaches_get_price_unretried(self):
        """Characterisation of QA-042: this is what happens today.

        One request, no retries, and the rate limit is reported to the agent as
        "Coin 'aave' not found. Use CoinGecko coin ID ...".
        """
        from app.tools import coingecko

        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(200, json=self.RATE_LIMITED_BODY)

        with mock_http(handler), instant_sleep() as delays:
            result = await coingecko.get_price({"coin_id": "aave"})

        self.assertEqual(len(attempts), 1, "a body-level 429 does not enter the retry ladder")
        self.assertEqual(delays, [])
        self.assertIn("not found", result["error"])

    @unittest.expectedFailure
    async def test_QA_042_get_price_must_not_report_a_rate_limit_as_coin_not_found(self):
        """QA-042 (HIGH): a quota error is reported as a nonexistent token.

        The HTTP-429 path already returns a correct "rate limit persisted"
        message; the body-level 429 bypasses it entirely because the status code
        is 200. ``coin_id not in data`` is then true and the tool tells the agent
        the coin does not exist.

        That is the worst possible mistranslation for this system: an agent told
        a token is not listed on CoinGecko will write that into its findings as
        a fact about the project rather than as a data gap, and the calibration
        ledger already shows the committee acting on INSUFFICIENT_DATA verdicts.
        """
        from app.tools import coingecko

        with mock_http(json_response(self.RATE_LIMITED_BODY)), instant_sleep():
            result = await coingecko.get_price({"coin_id": "aave"})

        self.assertIn("error", result)
        self.assertNotIn("not found", result["error"])
        self.assertIn("rate limit", result["error"].lower())

    @unittest.expectedFailure
    async def test_QA_042_get_token_info_must_not_return_a_null_success(self):
        """QA-042 (HIGH), the worse half: get_token_info reports success.

        There is no ``coin_id not in data`` guard on this path. The rate-limit
        body has no ``market_data`` key, so every metric resolves to None and the
        tool returns a complete success envelope -- with a CoinGecko source
        record attached (see QA-031). Supply, FDV and genesis_date all arrive as
        null with no error anywhere, and genesis_date being null is exactly what
        makes the structural gate skip its age check (QA-014).
        """
        from app.tools import coingecko

        with mock_http(json_response(self.RATE_LIMITED_BODY)), instant_sleep():
            result = await coingecko.get_token_info({"coin_id": "aave"})

        self.assertIn("error", result)

    @unittest.expectedFailure
    async def test_QA_042_body_level_429_must_be_retried_like_an_http_429(self):
        """QA-042 (HIGH): it is the same quota, so it deserves the same ladder."""
        from app.tools import coingecko

        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(200, json=self.RATE_LIMITED_BODY)

        with mock_http(handler), instant_sleep() as delays:
            await coingecko.get_price({"coin_id": "aave"})

        self.assertEqual(len(attempts), 5)
        self.assertEqual(delays, [2, 4, 8, 16])

    async def test_a_genuinely_absent_coin_is_still_reported_as_not_found(self):
        """Guard against over-correcting QA-042 into swallowing real 404s."""
        from app.tools import coingecko

        with mock_http(json_response({})), instant_sleep():
            result = await coingecko.get_price({"coin_id": "not-a-coin"})
        self.assertIn("not found", result["error"])


class CoinGeckoArgumentTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_coin_id_produces_a_specific_error(self):
        from app.tools import coingecko

        with mock_http(json_response({})):
            result = await coingecko.get_price({"coin_id": "not-a-coin"})
        self.assertIn("not found", result["error"])

    async def test_coin_id_is_lowercased_and_trimmed(self):
        from app.tools import coingecko

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params["ids"])
            return httpx.Response(200, json={"aave": {"usd": 1}})

        with mock_http(handler):
            await coingecko.get_price({"coin_id": "  AAVE  "})
        self.assertEqual(seen[-1], "aave")

    async def test_404_from_token_info_is_reported_as_not_found_not_as_a_status_code(self):
        from app.tools import coingecko

        with mock_http(json_response({}, status_code=404)), instant_sleep():
            result = await coingecko.get_token_info({"coin_id": "nope"})
        self.assertIn("not found", result["error"])

    async def test_token_info_survives_a_response_with_no_market_data(self):
        from app.tools import coingecko

        with mock_http(json_response({"name": "Ghost", "symbol": "gho"})), instant_sleep():
            result = await coingecko.get_token_info({"coin_id": "ghost"})
        self.assertEqual(result["name"], "Ghost")
        self.assertIsNone(result["market_cap_usd"])
        self.assertIsNone(result["genesis_date"])

    @unittest.expectedFailure
    async def test_QA_032_a_null_coin_id_must_not_become_a_live_query_for_none(self):
        """QA-032 (LOW): ``str(args.get("coin_id", ""))`` stringifies None to "none".

        The default only applies when the key is *absent*. A model that emits
        {"coin_id": null} triggers a real request for the coin id "none" and gets
        back "Coin 'none' not found" -- an error message that names a coin nobody
        asked about, and a wasted call against the rate limit.
        """
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={})

        from app.tools import coingecko

        with mock_http(handler):
            result = await coingecko.get_price({"coin_id": None})

        self.assertEqual(seen, [], "a request was issued for a null coin_id")
        self.assertIn("error", result)

    @unittest.expectedFailure
    async def test_QA_031_an_unavailable_currency_must_be_an_error_not_a_null_success(self):
        """QA-031 (MED): a fully-null price envelope is returned with no error key.

        CoinGecko answers 200 with the coin present but no quote in the requested
        currency. get_price returns every field as None, no "error", and a
        CoinGecko source record attached. Downstream,
        citations.extract_sources_from_tool_result only skips results carrying an
        "error" key -- so the report cites CoinGecko as the source for a price
        that was never returned. See test_citations.QA-031.
        """
        from app.tools import coingecko

        with mock_http(json_response({"aave": {"usd": 63.0, "usd_market_cap": 1}})):
            result = await coingecko.get_price({"coin_id": "aave", "currency": "eur"})

        self.assertIn("error", result)


class BinanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_symbol_is_rejected_before_any_request(self):
        from app.tools import binance

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        with mock_http(handler):
            self.assertIn("error", await binance.get_klines({}))
            self.assertIn("error", await binance.get_orderbook_depth({"symbol": "  "}))

    async def test_invalid_interval_is_rejected_before_any_request(self):
        from app.tools import binance

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        with mock_http(handler):
            result = await binance.get_klines({"symbol": "AAVEUSDT", "interval": "7h"})
        self.assertIn("Invalid interval", result["error"])

    async def test_symbol_separators_are_stripped_and_uppercased(self):
        from app.tools import binance

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params["symbol"])
            return httpx.Response(200, json=[])

        with mock_http(handler):
            await binance.get_klines({"symbol": "aave/usdt"})
            await binance.get_klines({"symbol": "aave-usdt"})
        self.assertEqual(seen, ["AAVEUSDT", "AAVEUSDT"])

    async def test_region_block_is_reported_distinctly(self):
        from app.tools import binance

        with mock_http(json_response({}, status_code=451)):
            result = await binance.get_klines({"symbol": "AAVEUSDT"})
        self.assertIn("region", result["error"])

    async def test_empty_candle_list_yields_a_zero_count_not_a_crash(self):
        from app.tools import binance

        with mock_http(json_response([])):
            result = await binance.get_klines({"symbol": "AAVEUSDT"})
        self.assertEqual(result["candle_count"], 0)
        self.assertIsNone(result["current_price"])

    async def test_empty_orderbook_is_an_error(self):
        from app.tools import binance

        with mock_http(json_response({"bids": [], "asks": []})):
            result = await binance.get_orderbook_depth({"symbol": "AAVEUSDT"})
        self.assertIn("Empty orderbook", result["error"])

    async def test_technical_levels_refuses_to_compute_on_thin_history(self):
        from app.tools import binance

        candles = [[i, "1", "1", "1", "1", "1", 0, "0", 0, "0", "0", "0"] for i in range(10)]
        with mock_http(json_response(candles)):
            result = await binance.compute_technical_levels({"symbol": "AAVEUSDT"})
        self.assertIn("Insufficient data", result["error"])

    @unittest.expectedFailure
    async def test_QA_030_a_400_must_not_be_reported_as_symbol_not_found(self):
        """QA-030 (MED): every 400 is translated to "symbol not found".

        Binance returns 400 for a malformed limit, a bad interval, and an unknown
        symbol alike. The tool reports all of them as
        "Symbol 'X' not found on Binance spot markets."

        The Technical Analyst reading that will conclude the token has no spot
        listing -- a materially wrong statement about liquidity and entry
        feasibility -- when the actual fault was in the arguments the model sent.
        The 400 body carries the real reason and is discarded.
        """
        from app.tools import binance

        body = {"code": -1130, "msg": "Illegal characters found in parameter 'limit'"}
        with mock_http(json_response(body, status_code=400)):
            result = await binance.get_klines({"symbol": "AAVEUSDT", "limit": -5})

        self.assertNotIn("not found on Binance", result["error"])

    @unittest.expectedFailure
    async def test_QA_033_limit_arguments_must_be_validated(self):
        """QA-033 (LOW): ``isinstance(requested_limit, int | float)`` lets bool through
        and silently discards a numeric string.

        - ``limit: true``  -> min(int(True), 500) == 1, one candle
        - ``limit: "500"`` -> not int|float, silently falls back to 200
        - ``limit: 0`` / ``limit: -5`` -> passed straight to Binance, which 400s,
          which QA-030 then mislabels as symbol-not-found
        """
        from app.tools import binance

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(int(request.url.params["limit"]))
            return httpx.Response(200, json=[])

        with mock_http(handler):
            await binance.get_klines({"symbol": "AAVEUSDT", "limit": True})
            await binance.get_klines({"symbol": "AAVEUSDT", "limit": "500"})
            await binance.get_klines({"symbol": "AAVEUSDT", "limit": 0})

        self.assertEqual(seen, [200, 500, 200], f"limit coercion produced {seen}")


class ErrorShapeConsistencyTest(unittest.IsolatedAsyncioTestCase):
    """Can an agent tell "no data" from "the call failed"?

    Three shapes exist today and they are not distinguishable:
      A. {"error": "<human sentence>"}          -- returned by the tool
      B. {"error": "Tool execution failed: ..."} -- synthesised by the registry
                                                    after an uncaught raise
      C. a success envelope with empty lists / null fields and no error key
    """

    async def test_defillama_fees_404_is_a_clean_no_data_error(self):
        from app.tools import defillama

        with mock_http(json_response({}, status_code=404)):
            result = await defillama.get_protocol_fees({"protocol": "nope"})
        self.assertIn("No fee data available", result["error"])

    @unittest.expectedFailure
    async def test_QA_029_get_tvl_must_handle_http_status_itself(self):
        """QA-029 (HIGH): get_tvl calls raise_for_status() with no handling at all.

        It is the primary DeFi metric tool and it is the only one of the eleven
        with zero status handling. An unknown slug (404), a DeFiLlama outage
        (5xx), and a rate limit (429) all leave the tool as an httpx exception,
        get converted by ToolRegistry.execute into the same generic
        "Tool execution failed: Client error '404 Not Found' for url ...", and are
        therefore indistinguishable to the agent.

        "This protocol is not on DeFiLlama" is a finding. "DeFiLlama is down" is a
        data gap. Collapsing them is how an agent ends up asserting a protocol has
        no TVL when the API was simply unavailable.
        """
        from app.tools import defillama

        with mock_http(json_response({}, status_code=404)):
            result = await defillama.get_tvl({"protocol": "not-a-protocol"})
        self.assertIn("error", result)
        self.assertNotIn("Tool execution failed", result["error"])

    @unittest.expectedFailure
    async def test_QA_028_web_search_must_distinguish_rate_limits_from_no_results(self):
        """QA-028 (HIGH): web_search has no status handling either.

        Brave returning 429 raises out of the tool. Brave returning zero results
        yields {"result_count": 0, "results": []} with no error. Only one of those
        two means "there is nothing to find", but the agent that sees the
        registry's generic wrapper for the first has no way to tell.
        """
        from app.tools import web_search

        with settings_override(brave_search_api_key="fake"), mock_http(json_response({}, status_code=429)):
            result = await web_search.web_search({"query": "aave"})
        self.assertIn("error", result)
        self.assertIn("rate limit", result["error"].lower())

    @unittest.expectedFailure
    async def test_QA_028_twitter_must_handle_statuses_it_does_not_enumerate(self):
        """QA-028 (HIGH): search_twitter handles 401/429/400 and nothing else.

        403 (suspended app, wrong access tier) and 5xx escape as httpx exceptions.
        The three statuses that *are* handled return clean {"error": ...} dicts,
        so the tool is inconsistent with itself depending on which failure occurs.
        """
        from app.tools import twitter

        with settings_override(x_bearer_token="fake"), mock_http(json_response({}, status_code=403)):
            result = await twitter.search_twitter({"query": "aave"})
        self.assertIn("error", result)

    async def test_missing_credentials_are_reported_as_errors_not_empty_results(self):
        """Correct today: an unconfigured tool says so rather than returning nothing."""
        from app.tools import twitter, web_search

        with settings_override(brave_search_api_key="", x_bearer_token=""):
            self.assertIn("error", await web_search.web_search({"query": "aave"}))
            self.assertIn("error", await twitter.search_twitter({"query": "aave"}))

    async def test_zero_results_are_a_success_envelope_in_every_search_tool(self):
        """Characterisation of shape C: this is what "no data" looks like today."""
        from app.tools import twitter, web_search

        with settings_override(brave_search_api_key="fake"), mock_http(json_response({"web": {"results": []}})):
            result = await web_search.web_search({"query": "aave"})
        self.assertEqual(result, {"query": "aave", "result_count": 0, "results": []})
        self.assertNotIn("error", result)

        with settings_override(x_bearer_token="fake"), mock_http(json_response({"meta": {"result_count": 0}})):
            result = await twitter.search_twitter({"query": "aave"})
        self.assertEqual(result["tweet_count"], 0)
        self.assertNotIn("error", result)

    @unittest.expectedFailure
    async def test_QA_035_registry_error_strings_must_not_embed_the_request_url(self):
        """QA-035 (LOW): the registry stringifies the httpx exception verbatim.

        httpx.HTTPStatusError.__str__ includes the full request URL and query
        string, so whatever the tool put in the query -- and, for tools that ever
        move a token into a query parameter, the token -- is copied into the LLM
        context window and from there into agent_outputs in Postgres.
        """
        from app.tools import web_search
        from app.tools.registry import ToolRegistry
        from app.llm import ToolDefinition

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(name="web_search", description="d", parameters={"type": "object", "properties": {}}),
            web_search.web_search,
        )
        with settings_override(brave_search_api_key="fake"), mock_http(json_response({}, status_code=429)):
            result = await registry.execute("web_search", {"query": "aave"})

        self.assertNotIn("api.search.brave.com", result.get("error", ""))


class NotionToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_note_rejects_slugs_gracefully(self):
        """Handoff section 9.5: agents pass "geodnet-prior-eval" as a page_id.

        The documented fix is a UUID guard returning a graceful no-note rather
        than a 404. Testing the behaviour, not the presence of the guard.
        """
        from app.tools import notion_tools

        result = await notion_tools.read_note({"page_id": "geodnet-prior-eval"})
        self.assertNotIn("error", result)
        self.assertEqual(result["title"], "No matching prior note")
        self.assertEqual(result["url"], "")

    async def test_read_note_accepts_both_dashed_and_undashed_uuids(self):
        """Both forms must get past the guard and attempt a real lookup."""
        from app.tools import notion_tools

        with settings_override(notion_api_key=""):
            for page_id in ("3830a58c-96ec-8123-a384-d8f217a43a6e", "3830a58c96ec8123a384d8f217a43a6e"):
                result = await notion_tools.read_note({"page_id": page_id})
                self.assertEqual(result, {"error": "Notion not configured"}, page_id)

    async def test_read_note_requires_a_page_id(self):
        from app.tools import notion_tools

        self.assertEqual(await notion_tools.read_note({}), {"error": "page_id is required"})

    @unittest.expectedFailure
    async def test_QA_034_search_notes_must_not_claim_a_database_it_did_not_search(self):
        """QA-034 (MED): the requested database is echoed back regardless.

        If notion_learnings_db is unset, db_id stays None and search_notion runs
        across *everything* -- but the result still says
        {"database": "learnings"}. An agent, and any later reader of
        agent_outputs, is told the search was scoped when it was not. Any
        unrecognised database value behaves the same way.
        """
        from unittest import mock

        from app.tools import notion_tools

        async def fake_search(query, database_id=None, limit=5):
            self.assertIsNotNone(database_id)
            return []

        with (
            settings_override(notion_api_key="fake", notion_learnings_db=""),
            mock.patch.object(notion_tools, "search_notion", fake_search),
        ):
            await notion_tools.search_notes({"query": "aave", "database": "learnings"})


if __name__ == "__main__":
    unittest.main()
