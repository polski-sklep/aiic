# Tree-4 cleanup passes — triage

**Tree 4** is `~/Projects/committee-orchestrator` on the Mac: `master` @ `e1c2b52`,
dirty, remote pointing at a VPS path that is not the deployment path. It is
**abandoned**. It holds eight uncommitted cleanup passes made on a base that
predates `agents/technical_analyst.py`, `api/calibration.py`,
`knowledge/calibration.py`, `tools/binance.py` and `utils/citations.py`'s
`sources` extraction.

**Method.** Per `PROJECT_DECISIONS.md` D3 and handoff §14.6, each pass was
assessed by *reading both implementations*, not by grepping for fix-text
(§14.2). Nothing was copied out of tree 4. Tree 4 was not modified, reset or
merged. Assessment made against `agent/architecture` @ `800b6e1`
(= `integration` base `5d3c033` + contracts doc).

**Verdicts.** `PORT NOW` — worth doing on this branch. `PORT LATER` — real
residual value, but the files belong to another branch or the change is bigger
than a cleanup. `REJECT` — the current base already does this, or the tree-4
version is worse.

---

## Summary

| # | Pass | Verdict | Owning branch for the work |
| --- | --- | --- | --- |
| 1 | DRY consolidation through `prompt_utils` | **PORT LATER** (partial) | unowned — `agents/{base,ray,chair,report_writer}.py` |
| 2 | Shared type consolidation in `utils/types.py` | **PORT LATER** (split) | `2a` unowned; `2b` `agent/persistence` + `agent/ui-report` |
| 3 | Unused-code removal | **PORT NOW** (`get_func`) / **PORT LATER** (`reload_personas`) | `agent/architecture` / `agent/personas` |
| 4 | Circular-dependency fix (`tools/contracts.py`) | **DONE** — ported in `3eff02f` | `agent/architecture` |
| 5 | Weak-type narrowing at JSON boundaries | **REJECT** for `tools/` · **PORT LATER** for `agents/reconciliation.py` | unowned |
| 6 | Error-handling narrowing | **PORT NOW** (registry) / **PORT LATER** (Telegram) | `agent/architecture` / unowned (`telegram_bot.py`) |
| 7 | Legacy/fallback cleanup in `llm/` | **REJECT** — already present, verbatim | — |
| 8 | Comment/doc cleanup | **REJECT** — net information loss; its one real change is already in | — |

Two of the eight (7, 8) and most of a third (5) are the false-alarm pattern the
handoff warns about in §14.2: the rebuild implemented the same thing, so a diff
against tree 4 shows a difference that is not a regression.

**Scope note.** Only pass 4 was ported in this workstream (D3). The two
`PORT NOW` items — `ToolRegistry.get_func` (pass 3) and the registry's lost
traceback (pass 6) — are both in `tools/registry.py`, which this branch owns,
and are ready to go as two one-line commits. They were deliberately **not**
executed here: this document is the deliverable and the porting order is the
orchestrator's to set. Everything else needs an ownership decision first.

---

## 1 — DRY consolidation through `prompt_utils` — PORT LATER (partial)

`backend/app/agents/prompt_utils.py` **exists on this base already** and is not
a tree-4-only file. What differs is its surface and its uptake.

| | tree 4 | this base |
| --- | --- | --- |
| helpers | 6 | 3 |
| callers | `base.py`, `ray.py`, `chair.py`, `report_writer.py`, `risk_officer.py`, `synthesis_agents.py` | `risk_officer.py`, `synthesis_agents.py` |

The three helpers this base lacks are `get_utc_today`,
`format_json_for_prompt` and `format_named_json_section`. The duplication they
were written to remove is still present here and is verifiable:

- `today = _dt.now(_tz.utc).strftime("%Y-%m-%d")` appears verbatim in
  `agents/base.py:62`, `agents/ray.py:23`, `agents/risk_officer.py:29`,
  `agents/synthesis_agents.py:29` — four copies, one line each.
- Trusted-accounts loading is implemented **three** ways: the
  `prompt_utils.load_trusted_accounts_section()` helper (used by
  `synthesis_agents.py:32`) and two hand-rolled `os.path.join(...)` blocks in
  `agents/base.py:75-81` and `agents/ray.py:29`.
- Prompt JSON dumping with a truncation limit is open-coded in
  `agents/chair.py:34,35,37` and `agents/report_writer.py:32`.

Tree 4 also moved the trusted-accounts read into `app/memory` as a cached
`load_trusted_accounts()` with an `_load_optional_file` helper, which is the
right home — `app/memory` already owns and `lru_cache`s every other memory file,
and `reload_memory()` clears their caches. The `prompt_utils` version re-reads
the file from disk on every prompt build and is invisible to `/api/memory/reload`.

**Why later, not now.** The payoff is in `agents/base.py`, `ray.py`, `chair.py`
and `report_writer.py`, none of which `agent/architecture` owns. `CONTRACTS.md`
§1 assigns no owner to them at all, so this needs an orchestrator call on who
takes it — and it should land after the branches that *do* touch `agents/`
(`agent/persistence` owns `orchestrator.py`) have integrated, to avoid
conflicting edits to the same prompt-building code.

**Do not** take tree 4's `prompt_utils.py` wholesale: its
`format_prior_outputs_section` is byte-identical to this base's apart from
`Any` → `JSONObject`, so only the three added helpers and the `app/memory`
relocation are actually new.

---

## 2 — Shared type consolidation in `utils/types.py` — PORT LATER (split in two)

Tree 4's `utils/types.py` adds, over this base: `ToolError`,
`SerializedAgentResult`, `AgentResultsByName`, and four coercion helpers
(`as_json_object`, `as_json_array`, `as_json_object_map`,
`as_json_object_list`). These are two different changes with different risk.

### 2a — `ToolError` — real, small, and in `agent/architecture` territory

`class ToolError(TypedDict, total=False)` is declared **six times** on this
base, once per tool module:

```
tools/binance.py:17       {error, details}
tools/coingecko.py:18     {error, details}
tools/twitter.py:31       {error, details}
tools/notion_tools.py:31  {error, details}
tools/defillama.py:14     {error}
tools/web_search.py:26    {error}
```

Four are identical; two are a narrower variant, and that divergence is
accidental rather than meaningful — `registry.execute` returns
`{"error": ...}` with no `details` for every tool regardless. One definition in
`app/utils/types.py`, re-exported through `app/tools/contracts.py` alongside
`ToolFunc`, is the natural completion of pass 4.

Not done in pass 4 deliberately: the task scoped `agent/architecture` to the
**import lines** of the tool modules, and this deletes six class bodies and
edits `app/utils/types.py`, which `CONTRACTS.md` §1 assigns to nobody. It needs
an explicit ownership grant, which is one sentence from the orchestrator.

### 2b — `SerializedAgentResult` / `AgentResultsByName` — cross-branch, defer

`orchestrator._ser()` (`agents/orchestrator.py:339-351`) builds the ten-key
agent-result dict, and three separate call sites re-read those same keys by
string: `api/evaluate.py:96,158-162`, `api/reports.py:237`,
`orchestrator.py:306`. A TypedDict there is genuinely useful — it is the shape
that crosses the process boundary into `agent_outputs`.

But `orchestrator.py`, `api/evaluate.py` and `models/__init__.py` are
`agent/persistence`'s, and `api/reports.py` is `agent/ui-report`'s. Both
branches are actively rewriting exactly these paths (`agent/persistence` has to
start passing a real `evaluation_id`, per `CONTRACTS.md` §3.1). Landing a type
across four files owned by two other branches mid-flight buys nothing and
guarantees conflicts. **Revisit after integration**, as a typing-only change
with no behaviour delta.

The `as_json_*` coercion helpers are the plumbing 2b needs and land with it.

---

## 3 — Unused-code removal — PORT NOW (one) / PORT LATER (one)

Both symbols are still dead on this base; neither is a false alarm.

- **`ToolRegistry.get_func`** — `tools/registry.py:24`. Defined, never called;
  `execute()` reaches into `self._tools` directly. `agent/architecture` owns
  `registry.py`, so this is a one-line deletion this branch can make. Held back
  from `3eff02f` only to keep that commit a pure dependency inversion.
  **PORT NOW**, as its own commit.

- **`reload_personas`** — `memory/agent_personas.py:62`. Defined, never called.
  Note the asymmetry that makes it look deliberate but is not:
  `memory/__init__.py:124::reload_memory` **is** wired to
  `POST /api/memory/reload` (`api/memory.py:30-32`), but that endpoint clears
  only the institutional-memory caches — it does not clear the persona cache.
  So editing a persona `.md` and hitting `/api/memory/reload` silently does
  nothing, which matters because `sync-committee` rsyncs persona markdown to the
  live box (handoff §7.3) and there is no way to pick it up short of a container
  restart. **The better fix is to call it, not delete it.** Either way the file
  is `agent/personas`'. **PORT LATER**, and flag it to them as a behaviour gap
  rather than dead code.

---

## 4 — Circular-dependency fix — DONE

Ported by reapplication in `3eff02f`. See `docs/adr/0001-tool-layer-contracts.md`.

The one thing deliberately **not** carried across: tree 4's `contracts.py`
imports `ToolError` from `app.utils.types`, which does not exist on this base
(see pass 2a). It was not invented.

---

## 5 — Weak-type narrowing at JSON boundaries — REJECT for `tools/`, PORT LATER for one function

This is the clearest false alarm in the set. A `diff` of, say,
`tools/coingecko.py` against tree 4 shows ~50 changed lines and looks like a
large missing improvement. Reading both files shows the opposite: **this base
already has the narrowing**, done independently and slightly differently.

`tools/{coingecko,defillama,twitter,web_search,notion_tools}.py` on this base
already declare `CoinGeckoPriceResult`, `ProtocolTvlResult`,
`TwitterSearchResult`, `WebSearchResult`, `SearchNotesResult` etc., already
annotate every tool as `async def f(args: ToolArguments) -> XResult | ToolError`,
and already use `cast` at the `resp.json()` boundary. `tools/binance.py` does
too and has no tree-4 counterpart at all. `llm/claude.py` likewise already uses
`JSONObject` and `cast(JSONObject, block.input)`.

Residue worth having, one function:

- **`agents/reconciliation.py:71`** — `def _flatten(d: object, prefix: str = ""):`
  has no return annotation and builds a bare `items = []`. Tree 4's version is
  `def _flatten(value: JSONValue, prefix: str = "") -> list[tuple[str, JSONValue]]`
  with an early return for the non-dict case. Strictly better, self-contained,
  no behaviour change. **PORT LATER** — `agents/reconciliation.py` is unowned,
  and this is not worth opening the file for on its own; fold it into pass 1.

The bare `context: dict` annotations on `get_system_prompt` in
`agents/synthesis_agents.py:25,96,167` and `agents/risk_officer.py:25` are the
same class of thing (`chair.py` and `base.py` already use `JSONObject`), and
also belong with pass 1.

---

## 6 — Error-handling narrowing — PORT NOW (registry) / PORT LATER (Telegram)

Two unrelated changes under one label.

- **`tools/registry.py::execute`** — this base logs `logger.error(f"Tool {name} failed: {e}")`,
  which discards the traceback. Tree 4 uses `logger.exception("Tool %s failed", name)`.
  That is a real diagnostic loss: a tool raising deep inside `httpx` currently
  produces one line naming the tool and the exception message, and the agent
  receives `{"error": "Tool execution failed: ..."}` either way — so the
  traceback is the *only* way to find where it broke, and it is thrown away.
  `agent/architecture` owns `registry.py`. **PORT NOW.** The broad
  `except Exception` itself must stay: a tool raising must not abort the
  evaluation, and every caller depends on the error being returned, not raised.

- **`telegram_bot.py`** — four `except Exception as e:` blocks
  (lines 53, 132, 200, 211) that swallow transport errors, HTTP status errors
  and JSON decode errors identically. Tree 4 splits them into
  `httpx.HTTPStatusError` / `httpx.HTTPError` / `ValueError` and adds the
  missing `raise_for_status()` calls. This is a genuine improvement and the base
  has not adopted it. **PORT LATER**: `telegram_bot.py` has no owner in
  `CONTRACTS.md` §1, it is the user-facing surface of a live system, and it is
  not import-validated by the backend container, so it needs its own
  verification story.

  One caveat if it is ported: tree 4's rewrite of `handle_message` changes the
  user-visible failure text and moves the `return` into the handlers. Behaviour
  is equivalent, but it is a Telegram-surface change, not a refactor, and
  should be exercised against the live bot rather than reviewed on paper.

---

## 7 — Legacy/fallback cleanup in `llm/` — REJECT

Already on this base, and the handoff says so at §9.5: *"the GitHub `router.py`
has no fallback at all"*. Verified by reading, not grepping:

- `llm/router.py` on this base selects **one** provider in `__init__`
  (`ClaudeProvider` if `anthropic_api_key` else `OpenAIProvider`), sets
  `self.provider_name`, logs it, and `complete()` is a single delegation. It is
  character-for-character what tree 4 produces. There is no try/except fallback
  to remove. Re-adding one would violate `CONTRACTS.md` §4.3.
- `llm/claude.py` on this base has already dropped the dead `TIER_TO_MODEL`
  constant and resolves models from `settings` in `_resolve_model`, matching
  handoff §9.5's "dead model strings" fix.
- The `temperature` handling is already correct: it is accepted in the signature
  and *not* forwarded into the Claude kwargs (`llm/claude.py:101-104` carries
  the comment explaining why), per `CONTRACTS.md` §4.4.

The only remaining difference in `llm/` is comment text and line wrapping. Not
worth a commit.

---

## 8 — Comment/doc cleanup — REJECT

Net negative, and it smuggles a behaviour change.

- **It deletes information.** Tree 4 replaces the `agents/orchestrator.py`
  module docstring — a twelve-line map of the pipeline naming every step, the
  gate, and which agent holds veto power — with four lines of prose that names
  no step. Same for `agents/guardrails.py`, where "runs after Protocol
  Resolution and before the full agent pipeline… to avoid wasting API credits"
  becomes "catches obvious disqualifiers early". For a system whose recurring
  failure mode is documentation drifting away from runtime (handoff §14.3), the
  specific docstring is the more valuable one.

- **Its one substantive change is already in.** The `main.py` CORS edit —
  dropping the hardcoded `http://localhost:3000` / `:3100` origins in favour of
  `allowed_origins = [origin for origin in (settings.frontend_url,) if origin]`
  — is present on this base at `main.py:49-53`. That change had no business
  being in a comment pass in the first place; it is `agent/devops` /
  `agent/security` territory.

What is left that is true: the base's `orchestrator.py` docstring still says
**"Ray Munger"** (line 11), a fossil of the Charlie Munger → Ray Dalio rename;
the class is `RayDalio`. Handoff §9.6 already records this. `orchestrator.py`
belongs to `agent/persistence` — one word, to be fixed in passing, not by a
dedicated pass.

---

## What tree 4 must never contribute

For the record, since a `diff -rq` against tree 4 will keep showing these:

`agents/technical_analyst.py`, `api/calibration.py`, `knowledge/calibration.py`,
`tools/binance.py` exist **only on this base**. `utils/citations.py` on this
base additionally extracts an explicit `sources` list from tool results
(lines 107-123) which tree 4 has no equivalent for. A file-level copy in either
direction is a silent feature rollback (handoff §14.6). Tree 4 is a reading
source and nothing else.
