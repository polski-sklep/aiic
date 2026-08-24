# QA findings

**Branch:** `agent/qa` · **Owner of this file:** `agent/qa`
**Status after pass 2:** 45 of 53 findings closed. 8 open, every one traceable to
an `@unittest.expectedFailure` test named after its id.
**Verified in:** container Python 3.12.13 (`docker compose -p aiic-qa run --rm --no-deps backend`).
Nothing here was validated on the Mac's 3.14.

```
Ran 272 tests
OK (expected failures=8)
```

Nothing was reported closed on the strength of a test flipping. For each of the
52 that flipped in pass 2 I read the change in the product file and asked
whether it addressed the defect or merely moved out from under the assertion.
Two had moved (QA-040, QA-011) and were rewritten rather than unmarked; the
detail is in §4.

---

## 1. Test inventory

| File | Tests | Open (expected fail) | Covers |
| --- | ---: | ---: | --- |
| `test_tools_http.py` | 35 | 0 | CoinGecko backoff and body-429, Binance, error shapes, Notion guard |
| `test_citations.py` | 34 | 0 | footnote merge, reindex, dedupe, source catalog |
| `test_agent_output_parsing.py` | 27 | 0 | `parse_output`, `extract_score` |
| `test_prompt_assembly.py` | 22 | 1 | prompt sections, conviction-weight roster, `_calc_score` |
| `test_tool_registry.py` | 20 | 5 | registration, containment, live roster, ToolRegistrar |
| `test_guardrails.py` | 15 | 0 | structural gate pass/block/crash |
| `test_reconciliation.py` | 14 | 0 | cross-agent metric grouping and divergence |
| `test_api_contracts.py` | 9 | 0 | `/openapi.json`, the tool-execution gate, `/api/projects` |
| `test_config.py` | 8 | 2 | settings resolution, config discipline |
| **`agent/qa` total** | **184** | **8** | |
| `test_calibration.py` (owned by `agent/calibration`) | 88 | 0 | not mine; runs clean alongside |
| **Suite total** | **272** | **8** | |

Hermeticity: `tests/_support.py` patches `httpx.AsyncClient` onto a
`MockTransport` **and** blocks `socket.connect`, `socket.create_connection` and
`socket.getaddrinfo`. A test that leaks a real request fails with
`NetworkAccessError` instead of hitting a live API. No test reads an API key from
the ambient environment.

---

## 2. Open findings

Eight. Each has a failing test; none can be closed by `agent/qa`.

| id | Sev | What breaks | File | Owner | Note |
| --- | --- | --- | --- | --- | --- |
| QA-026 | **MED** | `get_definitions` silently drops a name that does not match, so a typo in an agent's `tool_names` costs it a capability permanently and invisibly — no error, no log, and the agent's own output cannot mention a tool it was never offered | `tools/registry.py:33` | `agent/architecture` | **Press on this one.** Two-line fix. This is the silent-capability-loss class handoff §14.3 warns about, and nothing else in the system would ever surface it |
| QA-024 | MED | A tool returning `None` escapes the dict contract; `base.py` serialises it to the string `"null"` and hands that to the model as a tool result, with no error anywhere | `tools/registry.py:36` | `agent/architecture` | `execute` is typed `-> ToolResult` and does not enforce it |
| QA-025 | MED | Non-serialisable results pass containment and raise in `base.py::run` at `json.dumps(result, default=str)` — *outside* the registry's try — so one odd tool result fails the **entire agent** | `tools/registry.py:41` + `agents/base.py` | `agent/architecture` | `default=str` is consulted for values, not keys, and never for circular refs |
| QA-027 | LOW | `register()` silently overwrites a duplicate name and accepts a non-coroutine function; a sync tool fails at call time as `"object dict can't be used in 'await' expression"` | `tools/registry.py:19` | `agent/architecture` | Two tests |
| QA-037 | MED | `jwt_secret` defaults to `""` and nothing refuses to start, so a deployment that forgets `JWT_SECRET` signs tokens with an empty key | `config.py:28` | **unowned — needs assignment** | Sharper of the two config findings. `98390a6` removed the hardcoded value but left the empty default |
| QA-036 | MED | `database_url` default embeds `committee:committee_dev_pw` | `config.py:12` | **unowned — needs assignment** | Survived both `c62379c` and `98390a6`. Handoff §14.2 in miniature: two commits say the hardcoded credentials went, one is still here |
| QA-013b | MED | `_calc_score` has no defence of its own against a non-finite score | `agents/orchestrator.py` | `agent/persistence` | The `extract_score` fix (QA-013) closed the realistic path and is verified. This test constructs `AgentResult(score=float("nan"))` directly, so it pins defence in depth at the second layer. Worth keeping: `extract_score` is not the only way a score reaches `_calc_score` |
| QA-043b | LOW | `limit` in `format_prior_outputs_section` bounds list items and silently means nothing for a string | `agents/prompt_utils.py` | **unowned — needs assignment** | Not a failing test; pinned by a passing one. See §5 |

---

## 3. Closed findings

45 closed. All verified by reading the fix, not by the flip.

**Citations** (`utils/citations.py`) — QA-001, QA-002 (unmapped and dangling
markers now render as `UNRESOLVED_CITATION` and can never be resolved by a later
merge), QA-003 (`mapping.setdefault`, first definition wins), QA-004
(`_normalize_url` returns `""` for anything that is not a URL), QA-005
(`_dedupe_key` lowercases scheme and host only, strips fragment and trailing
slash), QA-006 (`_is_citation_position` — a bracketed integer followed by prose
is left alone), QA-007 (`_renumber_merged` establishes the invariant the function
relies on), QA-008 (normalises its input), QA-009 (`_coerce_footnote_id` rejects
bools and non-integral floats), QA-031 (`_result_carries_data` — an envelope of
request echoes attests to nothing).

**LLM output boundary** (`agents/base.py`) — QA-010 (`_strip_code_fence` handles
an unterminated fence), QA-011 (`_balanced_object_candidates` tracks string
literals and yields first-one-first), QA-012 (`_loads` catches `RecursionError`;
non-str input degrades instead of raising), QA-013 (bools rejected,
`math.isfinite`, 0–100 range enforced, each discard logged).

**Structural gate** (`agents/guardrails.py`) — QA-014 (`_genesis_age_days` handles
CoinGecko's bare `YYYY-MM-DD`; the 90-day gate now fires), QA-015, QA-016
(0 is a market cap, not missing data), QA-017, QA-018 (`_category_terms` reads
CoinGecko's `categories` as well as the caller's field).

**Reconciliation** (`agents/reconciliation.py`) — QA-019 (`_leaf_name` grouping,
so `metrics.tvl` and `protocol_data.tvl` finally meet), QA-020
(`max(abs(a), abs(b))` denominator — symmetric, order-independent), QA-021
(bools rejected), QA-022 (numeric strings coerced; `_flatten` descends into
lists), QA-023.

**Tools** — QA-028, QA-029, QA-035, QA-042 all closed by
`tools/http_errors.py`, a shared failure vocabulary (`not_found`, `no_data`,
`rate_limited`, `unavailable`, `bad_request`, `not_configured`) with an
`INCONCLUSIVE_KINDS` set and prose that states in words that nothing was learned.
`transport_failure` reports only the exception class name, which is what closes
QA-035. `body_rate_limited` closes QA-042 on the agent-facing path. Also QA-030
(Binance 400 no longer reported as symbol-not-found), QA-032, QA-033, QA-034.

**Prompts** — QA-044 (`_failure_note` keeps a failed agent visible), QA-045
(`NON_CONVICTION_SCORE_AGENTS` withholds the score at the prompt layer while
leaving the timing channel intact).

**API** — QA-040 (schema renders; see §4), QA-041 (`project_id: UUID`).

**Process** — QA-039 closed: CONTRACTS §3.6 now matches the twelve registered
tools. QA-038 (ownership gap) is **partly** closed: `agent/architecture` picked
up the tool bodies, but `config.py` and `agents/prompt_utils.py` still have no
owner, which is why QA-036, QA-037 and QA-043b have nobody to route to.

---

## 4. Two that flipped for the wrong reason

Both were rewritten, not unmarked.

**QA-040 — "tool execution endpoint must accept a request".** The forward-ref
bug is genuinely fixed and `/openapi.json` returns 200. But this test passed
because `api/tools.py` now returns **403**: having fixed the schema, the author
deliberately gated execution behind `TOOL_EXECUTION_OVER_HTTP_ENABLED`, because
the endpoint is unauthenticated arbitrary tool execution on a service with no
auth (SEC-03) and the 500 had been acting as an accidental control. That is the
right call, and it is not what the test's name claimed. Rewritten to assert the
gate — request *understood* (403, a decision) rather than *fatal* (500, a bug).
The companion expectation that an unknown tool yields 404 is now **obsolete
rather than unmet**: the gate runs before the registry lookup, so a caller
cannot enumerate the roster. Replaced with a test pinning that ordering.

**QA-011 — "two objects should prefer the first complete one".** Passed, but the
assertion was only `assertIn("score", out)`, which a `parse_error` dict would
have failed and almost nothing else would. Strengthened to assert *which* object
comes back, since first-one-first is documented behaviour that a change to
last-wins would otherwise pass silently.

---

## 5. The two contradicting characterisation tests

Both asserted the defect itself and could not coexist with their fixed siblings.

**`test_QA_004_repro_two_agents_unsourced_claims_merge`** — rewritten to assert
the new behaviour end to end: two agents citing `"N/A"` and `"n/a"` register no
footnotes at all, and each marker renders as `[unverified]`. The unit-level
sibling covers `_normalize_url`; this keeps the end-to-end consequence, which is
the thing that was actually harmful.

**`test_QA_042_body_level_429_reaches_get_price_unretried`** — deleted. Its
siblings assert the retry ladder (5 attempts, delays 2/4/8/16) and the corrected
message, which is the whole of its ground.

---

## 6. QA-043 — why the test changed instead of the code

Pass 1 asserted that `limit` should bound string fields. The fix implemented was
`MAX_PROMPT_FIELD_CHARS = 1000`, a hard ceiling on every rendered field
regardless of type. That closes the stated harm: prompt size is no longer
unbounded by agent verbosity.

Making the pass-1 assertion pass would mean reinterpreting `limit` from *items*
to *characters* at both call sites — `agents/synthesis_agents.py` and
`agents/risk_officer.py`, which both pass `("summary", "Summary", 3)`. Doing it
at one and not the other would truncate every data agent's summary to three
characters in the **veto-holding** agent's prompt: strictly worse than the
original defect. So the test now asserts the ceiling that exists, and the
residual is pinned separately as QA-043b by a passing test, so the semantics
cannot drift without someone noticing.

**The cross-owner change, precisely:** give the tuple a fourth element, or
replace `limit: int | None` with `(max_items, max_chars)`, and update both call
sites in the same commit. `prompt_utils.py`, `synthesis_agents.py` and
`risk_officer.py` are all unowned, so this needs an assignment before anyone
can do it.

---

## 7. Correction to relay: the Devil's Advocate

The note on `NON_CONVICTION_SCORE_AGENTS` in `prompt_utils.py` states that the
Portfolio Manager and the Devil's Advocate both "carry conviction weight". Half
of that is wrong, checked against the runtime:

- `portfolio_manager` **is** in `_calc_score`'s weights at 0.05. True, and it is
  what makes QA-045 matter.
- `devils_advocate` is **not** in `weights` at all, so it contributes nothing to
  the arithmetic. It is also absent from `exclude_from_scores`, so its score *is*
  surfaced in the per-agent scores — a real reason to withhold it from peer
  prompts, but a different reason from the one stated.

The fix itself is correct and important; only the justification overstates. Two
tests now pin the true roster
(`test_prompt_assembly.TechnicalAnalystExclusionTest`) so neither claim can drift.
Recorded as **QA-046** — documentation, not behaviour; owner of the comment is
whoever takes `prompt_utils.py`.

---

## 8. `test_tokenomics_e2e.py`

Unchanged from pass 1 and still **not a test**: it imports cleanly but contains
no `TestCase` and no `test_`-prefixed functions, so discovery collects 0 tests
from it. It is a live-network harness — `run_tool_smoke` calls CoinGecko and
DeFiLlama for real, `run_tokenomics_agent` and `run_orchestrator` spend real
Anthropic credits — and its docstring still says `cd committee-orchestrator/backend`,
which is the old tree (handoff §0).

Recommendation stands: rename to `tests/manual_e2e_smoke.py` so a runner can
never collect it, and fix the stale path. Not done here — it predates this
branch and `agent/devops` has CI wired against current filenames.

---

## 9. Running the suite

No new dependency. Python 3.12.13, no `pytest` in the image; everything is
stdlib `unittest` / `IsolatedAsyncioTestCase`, and `fastapi.testclient` comes
with `fastapi`.

```
docker compose run --rm --no-deps backend python3 -m unittest discover -s tests
```

`--no-deps` matters — the suite is pure and must not require Postgres or Redis.
Do not gate CI on `pytest` being installed; the image does not have it.

Still outstanding for `agent/devops`: `docker-compose.yml` bind-mounts
`./backend/app` but not `./backend/tests`, so a test edit needs an image rebuild.
Adding `- ./backend/tests:/app/tests` to the backend service would remove that.
