# QA findings — pass 1

**Branch:** `agent/qa` · **Owner of this file:** `agent/qa`
**Base:** `integration` merged at `8b6555d` (architecture, persistence, personas,
security, retrieval, calibration all landed)
**Verified in:** container Python 3.12.13 (`docker compose -p aiic-qa run --rm --no-deps backend`).
Nothing here was validated on the Mac's 3.14.

Every defect below was reproduced by running the code, not by reading it. Each
has a test in `backend/tests/` marked `@unittest.expectedFailure` and named after
its id. The suite is green: an expected failure is the record of the defect, and
it will flip to `unexpected success` the moment the defect is fixed, which is the
signal to close it.

**I have fixed nothing outside `backend/tests/`.** Where a defect sits in an
unowned file the "owner" column says so and the orchestrator needs to route it.

---

## 1. Test inventory

| File | Tests | Expected failures | Covers |
| --- | ---: | ---: | --- |
| `test_citations.py` | 34 | 11 | footnote merge, reindex, dedupe, source catalog |
| `test_tools_http.py` | 36 | 12 | CoinGecko backoff and body-429, Binance, error shapes, Notion guard |
| `test_agent_output_parsing.py` | 27 | 10 | `parse_output`, `extract_score` |
| `test_tool_registry.py` | 20 | 5 | registration, containment, live roster, ToolRegistrar |
| `test_prompt_assembly.py` | 18 | 4 | prompt sections, technical-analyst exclusion, `_calc_score` |
| `test_guardrails.py` | 15 | 8 | structural gate pass/block/crash |
| `test_reconciliation.py` | 14 | 7 | cross-agent metric grouping and divergence |
| `test_config.py` | 8 | 2 | settings resolution, config discipline |
| `test_api_contracts.py` | 7 | 4 | `/openapi.json`, `/api/tools`, `/api/projects` |
| **`agent/qa` total** | **179** | **63** | |
| `test_calibration.py` (owned by `agent/calibration`) | 74 | 0 | not mine; runs clean alongside |
| **Suite total** | **253** | **63** | |

Hermeticity: `tests/_support.py` patches `httpx.AsyncClient` onto a
`MockTransport` **and** blocks `socket.connect`, `socket.create_connection` and
`socket.getaddrinfo`. A test that leaks a real request fails with
`NetworkAccessError` instead of hitting a live API. No test reads an API key from
the ambient environment; `settings_override` sets them explicitly.

---

## 2. Defect table

Severity is judged by what a wrong answer costs, not by how hard it is to hit.

| id | What breaks | Input that breaks it | Sev | File:line | Owning branch |
| --- | --- | --- | --- | --- | --- |
| QA-045 | Technical Analyst's score is printed to the Portfolio Manager under "PRIOR AGENT SCORES" | `prior_agent_outputs` containing `technical_analyst` with a `score` | **HIGH** | `agents/synthesis_agents.py:177` | **unowned** |
| QA-042 | CoinGecko body-level 429 (HTTP 200, `status.error_code: 429`) reported to the agent as "Coin 'aave' not found"; `get_token_info` returns a null success envelope | `{"status": {"error_code": 429, ...}}` with HTTP 200 | **HIGH** | `tools/coingecko.py:89,120,155` | `agent/architecture` (see QA-038) |
| QA-040 | `/openapi.json`, `POST /api/tools/{name}` and its 404 path all return 500 | any request | **HIGH** | `api/tools.py:11` | **unowned** |
| QA-014 | 90-day minimum-age gate never fires on real data | `_token_data["genesis_date"] = "2026-08-01"` (CoinGecko's actual shape) | **HIGH** | `agents/guardrails.py:94` | **unowned** |
| QA-001 | Prose citations survive with no footnotes and resolve to the previous agent's source | agent B emits `"... [1]"` with `footnotes: []` | **HIGH** | `utils/citations.py:339` | **unowned** |
| QA-002 | Dangling `[3]` is left verbatim and becomes a valid reference to another agent's source once merged grows | text cites `[3]`, footnotes define only 1 and 2 | **HIGH** | `utils/citations.py:371` | **unowned** |
| QA-013 | No range or finiteness validation on scores | `"score": NaN`, `1e400`, `true`, `-1`, `8500` | **HIGH** | `agents/base.py:288-293` | **unowned** |
| QA-010 | Unterminated code fence deletes the closing brace, then recovery cannot fire | ```` ```json\n{\n "score": 85\n} ```` (no closing fence) | **HIGH** | `agents/base.py:256` | **unowned** |
| QA-019 | Cross-agent metrics are grouped by full flattened path, so differently-shaped outputs are never compared | `{"a": {"metrics": {"tvl": 1e8}}, "b": {"protocol_data": {"tvl": 9e8}}}` | **HIGH** | `agents/reconciliation.py:93` | **unowned** |
| QA-020 | Divergence is `abs(a-b)/a`, so flagging depends on agent dict order | `{"a": {"tvl": 100}, "b": {"tvl": 125}}` vs the reverse | **HIGH** | `agents/reconciliation.py:97` | **unowned** |
| QA-015 | Gate crashes on an explicit `None` pre-fetch block | `{"_price_data": None, "_token_data": None}` | **HIGH** | `agents/guardrails.py:42-43` | **unowned** |
| QA-028 | Tool error shapes are inconsistent; "no data" and "call failed" are indistinguishable | Brave 429, X 403 | **HIGH** | `tools/web_search.py:47`, `tools/twitter.py:63` | `agent/architecture` |
| QA-029 | `get_tvl` has no status handling at all — 404, 429 and 5xx are the same generic string | DeFiLlama 404 for an unknown slug | **HIGH** | `tools/defillama.py:64` | `agent/architecture` |
| QA-041 | `/api/projects/{id}` returns 500 instead of 422 on malformed input | `GET /api/projects/not-a-uuid` | MED | `api/projects.py:56` | **unowned** |
| QA-011 | Brace recovery spans `find("{")`..`rfind("}")`, so one brace in the surrounding prose discards a valid object | `{"score": 85}\nNote: I used {search_notes}.` | MED | `agents/base.py:264` | **unowned** |
| QA-012 | `RecursionError` and `AttributeError` escape `parse_output` (only `JSONDecodeError` is caught) | `"[" * 200000`; `None` | MED | `agents/base.py:262` | **unowned** |
| QA-016 | Market cap of exactly 0 is treated as missing data and passes the $1M minimum | `_price_data["market_cap"] = 0` | MED | `agents/guardrails.py:53` | **unowned** |
| QA-017 | Gate crashes on plausible types | `market_cap: "500000"`; `category: ["Meme"]`; `genesis_date: 1600000000` | MED | `agents/guardrails.py:55,81,94` | **unowned** |
| QA-018 | Mandate exclusion reads only the caller-supplied `category`, never CoinGecko's `categories` | memecoin with `category` unset | MED | `agents/guardrails.py:81` | **unowned** |
| QA-003 | Duplicate local footnote ids: last wins, prose points at the wrong URL, first source orphaned | two footnotes both `"id": 1` | MED | `utils/citations.py:365` | **unowned** |
| QA-004 | Non-URL footnote targets are accepted and collide case-insensitively across agents | `"url": "N/A"` and `"url": "n/a"` | MED | `utils/citations.py:24` | **unowned** |
| QA-005 | Dedupe lowercases the whole URL (merges distinct paths) and ignores trailing slash / fragment (splits identical pages) | `/Aave` vs `/aave`; `/x` vs `/x/` vs `/x#s` | MED | `utils/citations.py:71` | **unowned** |
| QA-006 | Any bracketed integer in prose that collides with a footnote id is rewritten | "only [2] of the five audits" renders as "only [4] of ..." | MED | `utils/citations.py:10` | **unowned** |
| QA-021 | Booleans are extracted as numeric metrics | `{"tvl_verified": true}` vs a real TVL → 499,999,900% divergence | MED | `agents/reconciliation.py:86` | **unowned** |
| QA-022 | Numeric strings, list-nested metrics and camelCase names are invisible to reconciliation | `{"tvl": "100"}`; `{"chains": [{"tvl": 1}]}`; `marketCap` | MED | `agents/reconciliation.py:71,86` | **unowned** |
| QA-023 | `build_case_context` crashes on `_price_data: None` (same root as QA-015) | `{"_price_data": None}` | MED | `agents/reconciliation.py:12` | **unowned** |
| QA-024 | A tool returning `None` escapes the dict contract; base.py serialises it to the string `"null"` for the model | a tool with a missing `return` | MED | `tools/registry.py:36` | `agent/architecture` |
| QA-025 | Non-serialisable results pass containment and raise in `base.py`, failing the *whole agent* | circular dict; tuple dict keys | MED | `tools/registry.py:41` + `agents/base.py:217` | `agent/architecture` |
| QA-026 | `get_definitions` silently drops unknown names — a typo in `tool_names` costs an agent a capability, permanently and silently | `get_definitions(["get_price", "get_pirce"])` | MED | `tools/registry.py:33` | `agent/architecture` |
| QA-030 | Every Binance 400 is reported as "Symbol 'X' not found on Binance spot markets" | 400 with `msg: "Illegal characters found in parameter 'limit'"` | MED | `tools/binance.py:73,145` | `agent/architecture` |
| QA-031 | An all-null success envelope still gets a source record attached, so the report cites CoinGecko for a datum that does not exist | `get_price(coin_id="aave", currency="eur")` where no EUR quote exists | MED | `tools/coingecko.py:120` + `utils/citations.py:96` | `agent/architecture` |
| QA-034 | `search_notes` echoes the requested database while having searched everything | `{"database": "learnings"}` with `notion_learnings_db` unset | MED | `tools/notion_tools.py:45` | `agent/architecture` |
| QA-036 | `database_url` default still embeds `committee:committee_dev_pw` after two commits that removed the other hardcoded credentials | default settings | MED | `config.py:12` | **unowned** |
| QA-037 | `jwt_secret` defaults to `""`; nothing refuses to start | `JWT_SECRET` unset | MED | `config.py:28` | **unowned** |
| QA-043 | The `limit` argument is a silent no-op for string fields | `("summary", "Summary", 3)` with a 500-char summary | MED | `agents/prompt_utils.py:70` | **unowned** |
| QA-007 | `reindex_citations` assumes `merged[i]["id"] == i+1`; a pre-populated list corrupts the id space | merged seeded with `{"id": 7, ...}` | LOW/MED | `utils/citations.py:343` | **unowned** |
| QA-044 | An agent whose requested fields are all missing/None vanishes from the section entirely | `{"onchain_analyst": {"score": None, "error": "..."}}` | LOW/MED | `agents/prompt_utils.py:52` | **unowned** |
| QA-008 | `reindex_citations` raises `KeyError` on footnotes not passed through `normalize_footnotes` | `[{"id": 1}]` | LOW | `utils/citations.py:346` | **unowned** |
| QA-009 | `int()` coercion turns `1.9` and `True` into id 1, manufacturing duplicates (feeds QA-003) | `[{"id": 1, ...}, {"id": 1.9, ...}]` | LOW | `utils/citations.py:312` | **unowned** |
| QA-027 | `register()` silently overwrites a duplicate name and accepts a non-coroutine function | two `register` calls for `dup`; a plain `def` tool | LOW | `tools/registry.py:19` | `agent/architecture` |
| QA-032 | `str(args.get("coin_id", ""))` turns `None` into the literal `"none"` and issues a live query for it | `{"coin_id": null}` | LOW | `tools/coingecko.py:94` | `agent/architecture` |
| QA-033 | `limit` coercion: `true` → 1 candle, `"500"` → silently 200, negatives passed straight through | `{"limit": true}`, `{"limit": "500"}`, `{"limit": -5}` | LOW | `tools/binance.py:58,137` | `agent/architecture` |
| QA-035 | Registry error strings embed the full request URL and query into the model's context and into `agent_outputs` | any tool that lets an `httpx` error escape | LOW | `tools/registry.py:42` | `agent/architecture` |

Process findings, no test:

| id | Finding | Sev |
| --- | --- | --- |
| QA-038 | **The ownership table in CONTRACTS §1 does not cover most of the code these defects are in.** `utils/citations.py`, `agents/base.py`, `guardrails.py`, `reconciliation.py`, `prompt_utils.py`, `synthesis_agents.py`, `config.py`, `api/tools.py`, `api/projects.py` and the *bodies* of every tool module have no owner. `agent/architecture` owns `tools/*.py` **import lines** only. 31 of the 43 defects above land on unowned files. | MED |
| QA-039 | CONTRACTS §3.6 says eleven tools are live. Twelve are registered — `semantic_search_notes` was added by `agent/retrieval` and put in `BaseAgent._base_tools`. The contract needs updating (orchestrator only). | LOW |

---

## 3. The three that matter most

### QA-045 — the Technical Analyst is influencing conviction

CONTRACTS §4.1 and handoff §3 both state the constraint: the Technical Analyst
must never influence conviction, only entry timing. It is enforced in
`orchestrator._calc_score` (verified — a technical score does not move the
weighted average) and **nowhere else**. `PortfolioManager.get_system_prompt`
renders `("score", "Score", None)` across the whole of `prior_agent_outputs`
with no filter, so the observed prompt is:

```
PRIOR AGENT SCORES:
[tokenomics_analyst]
  Score: 70
[technical_analyst]
  Score: 42

TECHNICAL ENTRY CONTEXT (timing only, not conviction):
[technical_analyst]
  Current entry quality: poor
```

The Portfolio Manager carries a 0.05 conviction weight and its judgment is a
conviction input. A technical score is reaching it as a peer score, immediately
above the disclaimer saying it must not. The constraint is about influence, not
about one formula, so this is a violation. `agents/synthesis_agents.py:177`,
unowned. Test: `test_prompt_assembly.TechnicalAnalystExclusionTest`.

### QA-042 + QA-028 + QA-029 — the agents cannot tell a failure from an absence

Three distinct error shapes exist and they are not distinguishable by a model:
a tool-authored `{"error": "<sentence>"}`, a registry-synthesised
`{"error": "Tool execution failed: Client error '429 ...' for url ..."}`, and a
plain success envelope with empty lists and null fields. The sharpest instance is
QA-042: CoinGecko's free tier answers an over-quota request with **HTTP 200** and
`{"status": {"error_code": 429}}`, `_get_with_backoff` only inspects the status
code, so the retry ladder is skipped and `get_price` tells the agent
*"Coin 'aave' not found"* — while `get_token_info`, which has no such guard,
returns a complete success object with every metric null and a CoinGecko source
attached (QA-031). `agent/calibration` already solved this for the history
endpoint in `knowledge/calibration.py::body_rate_limited`; the agent-facing tools
did not get the same treatment. An agent told a token is not listed will write
that into its findings as a fact about the project, and the calibration ledger
shows the committee already acting on `INSUFFICIENT_DATA` verdicts.

### QA-001 + QA-002 — citations silently point at the wrong source

`reindex_citations` early-returns when an agent supplies no footnotes, and leaves
any unmapped id verbatim. Both cases produce prose containing `[N]` that is then
resolved against the *merged* list — i.e. against a different agent's source.
Reproduced: agent A registers `coingecko.example/aave` as `[1]`; agent B emits
`"the entity is offshore [1]"` with an empty footnote list; B's citation now
links to A's CoinGecko page. The dangling variant is worse because it becomes
valid later: agent A cites `[3]` with two footnotes, and the moment a third
source enters `merged`, A's `[3]` starts resolving to it. This module decides
whether a report's evidence links to the right source and had no tests at all
before this pass.

---

## 4. Does `test_tokenomics_e2e.py` run?

**It is not a test.** It imports cleanly in the container and its API surface is
still valid (`Orchestrator.evaluate` and `TokenomicsAnalyst` both resolve), but
it contains no `TestCase` and no `test_`-prefixed functions, so
`python3 -m unittest discover -s tests` collects **0 tests** from it — verified
before this pass, when discovery reported `NO TESTS RAN`.

It is a manual live-network harness: `run_tool_smoke` calls CoinGecko and
DeFiLlama for real, `run_tokenomics_agent` and `run_orchestrator` spend real
Anthropic credits. Its module docstring also tells you to
`cd committee-orchestrator/backend`, which is the *old* tree (handoff §0); the
repo is `aiic`.

Recommendation: leave it in place but rename it out of the `test*.py` discovery
namespace — `tests/manual_e2e_smoke.py` — so it can never be picked up by CI, and
fix the stale path in its docstring. It costs money if a runner ever collects it
and calls its functions. I have not renamed it: it predates this branch and I did
not want to collide with `agent/devops` wiring CI against the current filenames.

---

## 5. Framework and dependency request

**No new dependency is needed.** The container has Python 3.12.13 and no
`pytest`; everything here is stdlib `unittest` /
`unittest.IsolatedAsyncioTestCase`, and `fastapi.testclient` comes free with
`fastapi`. Both runners find the suite from `backend/`:

```
python3 -m unittest discover -s tests        # verified, 253 tests
python3 -m pytest tests                      # would also collect these
```

For `agent/devops`: the CI command is

```
docker compose run --rm --no-deps backend python3 -m unittest discover -s tests
```

`--no-deps` matters — the suite is pure and must not require Postgres or Redis.
If pytest is ever added to `requirements-dev.txt` the tests need no changes, but
**do not gate CI on pytest being present**; the current image does not have it.

One request that is not a dependency: `docker-compose.yml` bind-mounts
`./backend/app` into the backend container but not `./backend/tests`, so every
test edit currently needs an image rebuild. Adding
`- ./backend/tests:/app/tests` to the backend service's volumes would make the
CI and local loop much faster. That file is owned by `agent/devops`; I have it
only in my gitignored `docker-compose.override.yml`.

---

## 6. Needs re-checking after further integration

- **QA-045, QA-043, QA-044** touch `agents/synthesis_agents.py` and
  `prompt_utils.py`. `agent/personas` landed changes described as "enforce
  data-agent independence"; I verified QA-045 against the post-merge tree, but
  if the prompt layer is revised again these need re-running.
- **QA-013's consequence test** exercises `orchestrator._calc_score`, owned by
  `agent/persistence`, which is actively changing (`evaluate()` now takes an
  `evaluation_id`). The test only reads `_calc_score` and should survive, but
  confirm after the next merge.
- **QA-040 / QA-041** are in `api/tools.py` and `api/projects.py`. If
  `agent/persistence`'s report-persistence work touches the API layer these may
  move.
- Nothing in this pass covers scoring policy, the orchestrator pipeline,
  calibration, persistence, personas or the report renderer. That is pass 2.
