# Paused: `agent/audit-trigger` and `agent/run-status`

Both stopped mid-task on 2026-08-26 at Jacob's request, to resume Saturday
2026-08-29 02:00 local. Nothing is lost. This is what the resuming agent needs.

## State

| | `agent/audit-trigger` | `agent/run-status` |
|---|---|---|
| Worktree | `worktrees/audit-trigger` | `worktrees/run-status` |
| Committed | `0843b2b feat(consistency): an in-process scheduler that actually runs the sweep` | *nothing yet* |
| Stashed | `stash@{0}` "WIP: audit-trigger paused 2026-08-26" | `stash@{0}` "WIP: run-status paused 2026-08-26" |
| In the stash | `knowledge/consistency_schedule.py`, `docs/operations.md`, `tests/test_consistency_schedule.py` | `agents/orchestrator.py`, `api/evaluate.py`, `models/__init__.py`, `migrations/0005_evaluation_run_health.sql`, `migrations/manual/`, `tests/test_run_status.py` |
| Local stack | `aiic-trigger` torn down, volumes removed | `aiic-status` torn down, volumes removed |

Restore with `git stash pop` in each worktree. **Neither branch is verified by
me** — both agents were stopped before reporting evidence. Treat every commit
and every stashed file as unreviewed.

Both were mid-sentence on their most important proof when stopped:

- **audit-trigger** was about to drive a genuine, unmocked `run_audit` failure
  through the real HTTP path to prove it cannot escape into the request path.
- **run-status** was about to prove its backfill against a production-seeded
  database.

Those two proofs are the ones that matter most. Do not accept either branch
without them.

## Why this work exists

`GET /api/consistency/due` on the live server answers:

```
{"due": true, "reason": "no audit has ever run", "corpus_size": 11,
 "policy": {"every_n_reports": 10, "every_n_days": 30}}
```

The policy Jacob asked for is encoded correctly and `audit_is_due()` answers
correctly. **Nothing has ever called it** — no crontab entry, no systemd timer,
no orchestrator hook, no bot command. `consistency_findings` is empty because
the sweep has never executed in production, not because the corpus is clean.

Separately, five production evaluations are recorded `status = 'completed'`
while their Report Writer failed:

```
Polkadot     5e6e4f2d  2026-04-14  429 quota exceeded
Hyperliquid  b028881a  2026-04-16  429 quota exceeded
Chainlink    b22be475  2026-06-01  429 quota exceeded
Aave         0f48a034  2026-06-11  429 quota exceeded
Hyperliquid  e2d96b62  2026-08-25  parse_error, fell back to raw_output
```

Four were re-run by hand the same day and succeeded. Nothing in the system
said anything was wrong.

## Open questions neither agent had answered yet

**audit-trigger**
- Boot behaviour: `due` is `true` right now, so a container restart loop must
  not fire a full audit every time. The decision and its reasoning were not
  yet reported.
- Advisory lock: two workers means two schedulers racing the findings ledger.
  `backend/migrations/` establishes the Postgres advisory-lock pattern; the
  proof that one runs and one skips was not seen.
- Whether Jacob should be notified when a sweep finds something.
  `telegram_bot.py` belongs to another branch — specify, do not edit.

**run-status**
- The semantics: is a failed Report Writer `failed`, or a distinct state? Every
  existing reader of `status` must be enumerated first — `api/evaluate.py`,
  `telegram_bot.py`, the Notion writer, the retrospective queries — because an
  enum value existing readers do not understand is its own defect.
- Is `e2d96b62` a failure or a degraded success? It has a `summary` and a
  `raw_output` but no parsed report.
- Retry: a 429 is transient and all four would likely have succeeded, but
  automatic retry against a quota-exhausted account burns remaining budget and
  Jacob has hit spend limits repeatedly. **Recommend with costs; do not decide.**
- **Backfilling the five rows is a production write and is Jacob's to run.**
  A migration `0005_evaluation_run_health.sql` and a `migrations/manual/`
  directory are in the stash, unreviewed.

## Regression bar

`main` at `2750109` is **355 tests, 6 expected failures**, ruff clean, mypy
clean over 53 source files — measured, not assumed. `backend/tests` is **not**
mounted by compose (only `backend/app` is), so a test-file change needs
`docker compose build backend` or a `-v $(pwd)/backend/tests:/app/tests` mount.
A stale image will cheerfully report an old count; that already happened once.

Any migration must be forward-only and prove out on a fresh volume **and** a
volume seeded to match production, with the re-run a no-op.

## Environment

The Anthropic API budget is exhausted until **2026-09-01**, so no evaluation
can be run and `run_audit(verify=True)` will make model calls — use
`verify=False` to exercise wiring. Production Postgres is read-only:
`ssh root@100.95.239.105 "docker exec committee-postgres psql -U committee -d committee -c '...'"`.

The live server is on **`cbcf2f3`**. Everything from 26 August is on
`origin/main` (`2750109`) but **not deployed**. Deploy was to follow these two
branches landing.
