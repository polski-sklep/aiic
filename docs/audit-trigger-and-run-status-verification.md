# `agent/audit-trigger` and `agent/run-status` — verification report

Resumed 29 Aug 2026, replacing `docs/PAUSED-audit-trigger-and-run-status.md`.
Both branches were paused mid-proof on 26 August and neither had been verified
by anyone. Every commit and stashed file was treated as unreviewed and
re-derived.

**Both branches are now verified and ready to merge.** Nothing has been pushed
and nothing has been deployed. Three things are left for Jacob and they are
listed at the end.

| | `agent/audit-trigger` | `agent/run-status` |
|---|---|---|
| Head | `13048ed` (was `0843b2b`) | `02a3b12` (was nothing) |
| Tests | 386, 6 expected failures | 393, 6 expected failures |
| ruff / mypy | clean / clean, 54 files | clean / clean, 53 files |
| Baseline | `2750109` = 355 tests, 6 expected failures | same |
| Stash | popped, empty | popped, empty |
| Local stack | `aiic-trigger` torn down, volumes removed | `aiic-status` torn down, volumes removed |

Ports: the parent checkout's own `committee-*` stack was running on
55432/56379/58100 throughout and was not touched. The two verification stacks
used 55434/56381/58102 and 55433/56380/58101.

---

## The two missing proofs

### 1. audit-trigger — a genuine unmocked `run_audit` failure through the real HTTP path

The existing 29 tests are hermetic; every collaborator is patched. What had
never been shown was that a real failure cannot escape into the request path or
kill the lifespan. Driven against a real container with a real Postgres, with
nothing mocked.

**Setup.** The local database was seeded from a read-only `pg_dump` of the
production `projects` / `evaluations` / `agent_outputs` / `reports` tables — 13
projects, 20 evaluations, 276 agent outputs. Corpus as the sweep sees it: 14.

**First, the happy path, which had also never been shown in a container:**

```
08:54:46  MIGRATIONS: 4 applied, 0 already present, 0 error(s)
08:54:46  CONSISTENCY SCHEDULE: started — first tick in 300s, then every 3600s
                                (no sweep on boot, by design)
08:54:46  Application startup complete.
08:59:46  CONSISTENCY SCHEDULE: sweep starting — no audit has ever run (corpus 0, verify=True)
08:59:46  CONSISTENCY SCHEDULE: sweep complete in 0.0s
```

Exactly 300 seconds between startup and the first tick. No sweep on boot.

**A real sweep over the real corpus:**

```
POST /api/consistency/schedule/tick?verify=false   -> HTTP 200, 0.42s
{"action": "swept", "corpus_size": 14, "claims_extracted": 40,
 "conflicts_found": 4, "findings_new": 4, "findings_existing": 0}
```

**This is a finding, not just a proof.** Production `consistency_findings` is
empty and `consistency_audit_runs` has **zero rows** — re-confirmed on the live
database today. The corpus is not clean; nothing has ever looked at it. Run
against the real production corpus the sweep finds **four contradictions**, all
Hyperliquid: two `high` on `perp_market_share_pct`, one `medium` on
`perp_market_share_pct`, one `medium` on `volume_30d_usd`. Re-running is a
no-op: `findings_new: 0, findings_existing: 4`.

**Then the failure, injected genuinely by renaming a table the sweep writes to:**

```
ALTER TABLE consistency_findings RENAME TO consistency_findings_hidden;

POST /api/consistency/schedule/tick?force=true     -> HTTP 200
{"action": "error", "stage": "sweep",
 "error": "(sqlalchemy...asyncpg.ProgrammingError) <class
   'asyncpg.exceptions.UndefinedTableError'>: relation
   \"consistency_findings\" does not exist ..."}
```

Every assertion that matters, checked immediately afterwards:

| Question | Result |
|---|---|
| Did it escape into the request path? | No. `/health`, `/api/projects`, `/api/consistency/due` all still HTTP 200 |
| Did it kill the lifespan? | No. `running: true`, `next_tick_at` unchanged and in the future |
| Was a durable trace left? | Yes. `consistency_audit_runs` gained a `status='failed'` row carrying the full error |
| Did the failure buy 30 days of silence? | No. With only the failed row present, `/api/consistency/due` still answers `{"due": true, "reason": "no audit has ever run"}` |
| Does it recover? | Yes. Table restored, next tick swept normally, `findings_existing: 4` |

### 2. run-status — the backfill against a production-seeded database

A volume was seeded from the production dump and then rewound to production's
**exact** schema state: `run_health` dropped, its index dropped, the `0005`
ledger row deleted — leaving migrations `0001,0002,0003,0004` and 20
evaluations, which is what the live database holds today.

```
FIRST RUN   applied=['0005'] skipped=['0001','0002','0003','0004'] errors=[]   exit 0
SECOND RUN  applied=[]       skipped=['0001'..'0005']              errors=[]   exit 0
```

Also proven on a **fresh** volume: `init.sql` runs, then `0005` applies on top,
and the column and the partial index both exist. Forward-only, idempotent, and
a no-op on re-run — on both volumes.

**The backfill itself, run against that production-seeded volume:**

```
--- as it ships, ending in ROLLBACK ---
UPDATE 5      (completed -> report_failed)
UPDATE 19     (run_health reconstructed)
ROLLBACK
  -> database afterwards: 19 completed / 1 failed / 0 run_health.  Nothing moved.

--- ROLLBACK swapped for COMMIT ---
UPDATE 5 / UPDATE 19 / COMMIT
  -> 14 completed / 5 report_failed / 1 failed;  19 rows carry run_health

--- run a second time ---
UPDATE 0 / UPDATE 0 / COMMIT      (idempotent; the error text is written once)
```

Exactly the five expected rows move, with the right reasons — four
`call_failed`, one `unparseable`. The guard also works: with `run_health`
absent, the script now aborts on statement 2 (`ON_ERROR_STOP`) and the statuses
are untouched.

---

## Open questions, and the evidence that settled each

### audit-trigger

**Boot behaviour — no sweep on boot.** `audit_is_due` answers `due: true`
today, so a boot-time sweep fires on every restart, and the backend runs
`restart: unless-stopped`. A crash loop would mean a full-corpus scan plus a
burst of CoinGecko calls per restart, and CONTRACTS §2.7 measured CoinGecko
429ing on the *fourth* call at 8-second spacing. The loop instead waits
`STARTUP_DELAY_SECONDS = 300` and then ticks hourly — observed above, 08:54:46
to 08:59:46. Five minutes against a policy measured in tens of days starves
nothing. The durable guard is the policy itself: after the first successful
sweep every later boot is told "not due" for 30 days.

**Concurrency — proven against real Postgres, not a mock.** An independent psql
session took the real lock and held it:

```
SELECT pg_try_advisory_lock(81002027);
  pg_locks:  advisory | 81002027 | granted = t

POST /api/consistency/schedule/tick?force=true   -> HTTP 200, immediate
{"action": "skipped", "why": "locked", ...}
  -> consistency_audit_runs unchanged. No error, no queueing.
```

When that session ended, `pg_locks` showed **0** advisory locks on the key and
the next tick swept normally. Session-scoped, so a worker that dies mid-sweep
leaves no stale lock to clear by hand. The key (81002027) is deliberately not
the migration runner's (81002026), so a sweep cannot stall a booting backend —
asserted by a test.

**The policy is asked, never restated.** `test_the_scheduler_source_contains_no_cadence_of_its_own`
reads the scheduler's own source and fails if `AUDIT_EVERY_N_REPORTS` or
`AUDIT_EVERY_N_DAYS` appears in it. Verified by reading: nothing in
`consistency_schedule.py` knows what 10 or 30 mean.

**`SWEEP_VERIFY = True` costs nothing — verified, and now pinned.**
`consistency.py`'s docstring calls the adjudication layer "external / LLM,
paid", which is what the default was originally weighed against. As
implemented, `verify_candidate` reaches DeFiLlama and CoinGecko over httpx and
nothing else. Proven: the container held **no `ANTHROPIC_API_KEY` at all** and
completed a `verified: true` sweep whose only outbound host was
`api.llama.fi`. A new test asserts no LLM import or call appears in that path,
because if it ever changes an hourly loop starts spending money quietly.

**Notification — specified, not made.** `telegram_bot.py` belongs to
`agent/bot-queue` / `agent/notion-*` / `agent/report-delivery`. The hook already
exists: a sweep that finds something logs at `WARNING` with the text
`CONSISTENCY SCHEDULE: N NEW cross-report contradiction(s) recorded`. The exact
edit is in the appendix below. **Recommendation: yes, but only for
`findings_new > 0`.** The sweep will be silent for months at a time; an
unconditional message trains it to be ignored.

### run-status

**Every reader of `status`, enumerated before any value was added.**

| Reader | Test | Behaviour on `report_failed` |
|---|---|---|
| `api/reports.py::_load_report_parts` | `!= "completed"` | 409 "Evaluation status is report_failed". **Verified over HTTP** |
| `api/reports.py::_list_report_rows` | `== "completed"` | Row drops off the list. Verified: 14 rows listed, none of the five |
| `knowledge/history.py::_build_prior` | `!= "completed"` | Prior marked unusable, reason includes `error`. Correct |
| `api/evaluate.py` GET | passthrough | Returns the string. Safe |
| `api/projects.py` | passthrough | Returns the string. Safe |
| `telegram_bot.py` | **never reads it** | See the defect below |
| Notion writer | never reads it | No change |

None enumerates the failure values, so a reader that has never heard of
`report_failed` still classifies it as not-a-success — which is correct.

**What this actually fixes, measured rather than argued.** Asked for the
Polkadot row today, with `status = 'completed'` as production has it, the
report endpoint returns **HTTP 200** and a 371-byte document:

```
# Investment Committee Report: Polkadot
**Date:** 2026-08-29           <- the day you asked, not the day it ran
## DECISION: N/A
- **Conviction:** N/A   - **Position Size:** N/A   - **Entry Strategy:** N/A
**Chair's Reasoning:** N/A
**Overall Score: N/A**
```

It looks like a report. After the change the same request is a 409 naming the
status. That is the whole case for the branch in one output.

**Terminal versus degradation.** Terminal — no artefact: the Report Writer
(nothing else assembles sections; no redundancy) and the structural gate.
Degradation — costs coverage, not the artefact: the eight data agents
(CONTRACTS §4.2 makes their independence the design, and synthesis tolerates
gaps) plus `maturation_scorer`, `devils_advocate`, `portfolio_manager` and
`ray_dalio`. The Chair is neither and keeps its existing `CHAIR_FAILED`
outcome. `risk_officer` is deliberately in neither set: an agent that never
answered reads as an agent that cleared the project, which is settled decision 1
(veto on presence of danger, never absence of evidence) with the sign flipped.
It is recorded in `run_health.risk_officer_ran` and reported, not decided.

**`e2d96b62` is a failure, not a degraded success.** The agent-by-agent record
settles it. Twelve agents returned full structured output and scored — the
score weight covered is **1.000**, a whole committee. But *three* agents hit the
same wall on the same run and all three fell back to `raw_output`:

```
report_writer     parse_error   22,382 bytes
ray_dalio         parse_error   11,290 bytes
committee_chair   parse_error   11,480 bytes
```

There is no report, no second opinion and no verdict — the Chair produced no
`decision` key at all. `summary` and `raw_output` are what a parse failure
leaves behind, not a degraded report. `agents/base.py` already settled the
identical question one stage later: a Chair that hits its ceiling mid-JSON is
CHAIR_FAILED and stays out of the ledger, because a parse failure is not a
verdict. It is not a report either.

**A correction to the branch's own claim.** The code asserted "Not transient:
the same prompt will hit the same ceiling." The database contradicts that. The
same project was re-run two hours later as `be8210d4`:

```
e2d96b62  16:04  report_writer  22,382 bytes  parse_error
be8210d4  18:07  report_writer  55,345 bytes  parsed cleanly
```

2.5x the output, no truncation, same day. The ceiling is stochastic, not
deterministic, and the difference matters — it is "re-running is futile" versus
"re-running is the remedy". The docstring has been corrected.

**A second correction, to the backfill.** The manual script had already drifted
from `report_deliverable_state` in two ways: it accepted `"sections": {}` as a
report, and it counted an `INSUFFICIENT_DATA` chair as having decided. Neither
shape exists in today's corpus — verified, zero rows — so both were **latent,
which is exactly how this class of defect stays invisible until it isn't.** The
script now builds one temp view matching the Python predicate for predicate,
and every statement reads it. The two implementations were then diffed row by
row against the production-seeded volume: **19 evaluations x 12 keys, zero
mismatches.** Three new tests pin the rosters hermetically, because what
actually drifts is someone adding a ninth data agent or re-weighting the score
table.

---

## Findings this work turned up that nobody was looking for

**1. The consistency corpus is not clean.** Four contradictions, all
Hyperliquid, sitting in production right now and invisible because the sweep
has never run. See proof 1.

**2. `ray_dalio` is the worst agent in the system and nothing records it.**
Across the corpus:

```
agent            runs  parse_failures  call_failures
ray_dalio          18               4              5     <- 9 of 18 unusable
committee_chair    19               1              6
report_writer      19               1              4
every other agent  19               0            4-5
```

Ray failed to parse on **both** Hyperliquid runs on 25 August, including the
one that otherwise succeeded. A 22% parse-failure rate against 0% for every
data agent is not noise. `build_run_health` will now surface it in
`degraded_only_failures` going forward, but the cause is unexamined and belongs
to whoever owns `ray_dalio`.

**3. Degradation is current, not historical.** The branch cites three damaged
runs from before the pause. The backfill's own preview finds **eleven**, and
`Kamino e1b7ac31` and `Arbitrum 40eaf3d8` (both 27 August, both after the
pause) are among them. Kamino: 2 agents failed, `chair_decided: false`. This is
happening now.

**4. `telegram_bot.py` never reads `status`.** It says `EVALUATION COMPLETE`
on any HTTP 200. On all five bad runs it announced completion. After this
branch it will still say COMPLETE and then hand over a report link that 409s.
The exact edit is in the appendix; it is not mine to make.

**5. `evaluations.run_health` is not mirrored into `init.sql`.**
`docs/operations.md` §14 states the convention: "Added a schema change | edit
`init.sql` **and** add a migration, same commit", and `init.sql` carries
`-- Mirrored from backend/migrations/0003_...` for the precedent. This is
**not a functional defect** — verified on a fresh volume, migration 0005
applies on top of `init.sql` and the column exists — but it breaks the
convention. `init.sql` belongs to `agent/persistence` (CONTRACTS §1), which is
closed, so the edit is specified in the appendix rather than made.

---

## What the budget blocked

The Anthropic budget is exhausted until 2026-09-01, so **no evaluation was
run**. Nothing in either branch's changed code path needs one:

- The consistency sweep and its verification make no model calls — proven, the
  container had no API key.
- `run_audit(verify=True)` was exercised in full.
- The orchestrator's status/`run_health` logic was exercised by 38 hermetic
  tests plus the SQL cross-check against 19 real production runs.

**Not exercised, and the one gap worth naming:** `Orchestrator.evaluate()` has
never been run end-to-end against a live model with the new status code in
place. The path is covered by tests with a stub router and the values it writes
were diffed against real historical data, but the first genuine
`report_failed` written by the pipeline itself will happen in production.

---

## For Jacob — three decisions, none taken

### 1. Run the backfill (production write)

Proven locally, ends in `ROLLBACK`, and a test asserts it still does. Procedure
in `backend/migrations/manual/README.md`. Dump first. Note the preview names 5
rows to move and 11 degraded runs to record, and that the guard requires
migration 0005 — so this runs **after** a deploy, not before.

### 2. Automatic retry on a 429 — recommended, with the cost stated

**Recommendation: do not add automatic retry. Add a re-run *prompt* instead.**

The four 429 runs consumed **zero tokens** — every agent's call failed, so they
cost nothing at all. A re-run is therefore a full-price run from scratch.
Priced at Anthropic list (`pricing.py`, taken 2026-08-27) from the real token
counts of the successful Hyperliquid run:

```
be8210d4   opus-4-8     in 550,307  out 51,406   $4.04
           sonnet-4-6   in 460,120  out 31,700   $1.86
                                         TOTAL   $5.89
```

That is a **floor**: `tokens_input` is the uncached remainder only and
cache-read tokens are not persisted. Call it $6–10 per re-run.

The case against automatic: a 429 means the quota is already gone, so an
immediate retry is the least likely moment to succeed, and a retry loop against
an exhausted account is how a spend limit becomes a spend spiral. Jacob has hit
spend limits repeatedly. The case for: all four were re-run by hand the same day
and all four succeeded, so the retry does work — just later, and with a human
deciding.

The middle path this branch already enables: the run is now recorded
`report_failed` with a reason, so the bot can *say* "no report — 429, re-run
costs about $6, reply /retry" and let Jacob spend the money on purpose. That
needs the `telegram_bot.py` edit below and no orchestrator change.

**This is a spending decision and it is not mine.**

### 3. Deploy

Still pending and still Jacob's. The live server is on `cbcf2f3`; `origin/main`
is at `2750109` or later and the parent checkout is now at `b833208` with
`agent/run-cost` merged. Per D14 the deploy is:

```
git pull --ff-only && docker compose up -d --build backend
```

Note that shipping `agent/audit-trigger` **starts the sweep on the live
server** five minutes after the restart, and the first sweep will record the
four Hyperliquid findings above. That is the intended behaviour and it is worth
knowing before it happens rather than after.

---

## Appendix — edits specified but not made, in files this branch does not own

**A. `telegram_bot.py` (owner: `agent/bot-queue`).** After line 882
(`data = r.json()`), before line 888 (`rec = data.get(...)`):

```python
            run_status = data.get("status", "completed")
            if run_status != "completed":
                # The pipeline finished but produced no report. Before
                # agent/run-status this arrived as "completed" and the bot
                # said EVALUATION COMPLETE over an empty document.
                await update.message.reply_text(
                    "EVALUATION DID NOT PRODUCE A REPORT: %s (%s)\n\n"
                    "Status: %s\n%s\n\n"
                    "The other agents' output is saved and the run can be "
                    "re-adjudicated. A re-run costs roughly $6-10."
                    % (name, ticker, run_status,
                       (data.get("error") or "")[:300])
                )
                return
```

**B. `telegram_bot.py` — consistency notification (same owner).** Poll
`GET /api/consistency/schedule` and message only when
`last_sweep_summary.findings_new > 0`, quoting `GET /api/consistency/findings`.
The scheduler already logs this at `WARNING`; nothing in the scheduler needs to
change.

**C. `backend/init.sql` (owner: `agent/persistence`).** In the `evaluations`
table, after `error TEXT`:

```sql
    -- Mirrored from backend/migrations/0005_evaluation_run_health.sql.
    run_health JSONB,
```

and after the table, mirroring the migration's partial index:

```sql
CREATE INDEX idx_evaluations_status_not_completed
    ON evaluations (status, created_at DESC) WHERE status <> 'completed';
```

**D. `backend/app/config.py` (owner: `agent/devops`, CONTRACTS §3.5).** The
scheduler's kill switch is currently a module constant, so turning it off needs
a code edit and a rebuild. Recommend a `Settings` field
`consistency_scheduler_enabled: bool = True` plus a line in `.env.example`, and
`SCHEDULER_ENABLED` reading it through `get_settings()`.

---

## Reproducing any of this

```bash
cd /Users/Jacob/Projects/aiic/worktrees/<audit-trigger|run-status>
cp ../../.env . && printf 'CONTAINER_PREFIX=aiic-x\nPOSTGRES_HOST_PORT=55434\nREDIS_HOST_PORT=56381\nBACKEND_HOST_PORT=58102\n' >> .env
printf 'services:\n  backend:\n    volumes:\n      - ./backend/tests:/app/tests:ro\n' > docker-compose.override.yml
export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin
docker compose -p aiic-x up -d --build
docker compose -p aiic-x run --rm --no-deps backend python3 -m unittest discover -s tests
make lint && make typecheck
docker compose -p aiic-x down -v
```

`backend/tests` is not mounted by the base compose file — only `backend/app` and
`backend/migrations` are — so the override above is required or a test-file
change is invisible and the run reports a stale count.
