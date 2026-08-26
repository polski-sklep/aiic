# Paused: `agent/consistency-audit`

Stopped mid-task on 2026-08-26 at Jacob's request. Nothing is lost; this is
what to hand the next agent so it does not restart from zero.

## State

| | |
|---|---|
| Branch | `agent/consistency-audit`, worktree `worktrees/consistency-audit` |
| Committed | `b91204b fix(consistency): a date-attribution finding must report its own periods` |
| Uncommitted | stashed as `stash@{0}` — "WIP: consistency-audit paused 2026-08-26" |
| Files in the stash | `knowledge/consistency.py`, `agents/reconciliation.py`, `test_consistency.py`, `test_reconciliation.py` |
| Local stack | `aiic-audit` and `aiic-consist` torn down, volumes removed |
| Production | untouched — all Postgres access was read-only `SELECT` |

Restore with `git stash pop` in that worktree.

## Task 1 — `detect_conflicts` crash — COMMITTED, NOT VERIFIED BY ME

`b91204b` claims to fix it. **I stopped the agent before it reported, so I
have not seen the evidence and have not verified it myself.** Treat it as
unreviewed. The defect it addresses:

```
claims: [('Hyperliquid','perp_market_share_pct',44.0,'2026-01','eval-a'),
         ('Hyperliquid','perp_market_share_pct',44.0,'2026-06','eval-b')]
CRASH: UnboundLocalError cannot access local variable 'periods'
```

`periods` is bound only inside the earlier `for clashing in clusters:` loop
and read inside `for same_value in seen.values():`. With no value conflict it
was never bound. When a value conflict *does* exist it leaks the previous
loop's value into the date-attribution finding — and `period` feeds
`fingerprint_of`, so the finding's stable identity derives from an unrelated
bucket. This is the Hyperliquid 44%-on-two-dates case the module exists to
catch.

`consistency_findings` is **empty in production (0 rows, verified)**, so a
fingerprint change orphans nothing.

## Task 2 — shared binding rules — IN THE STASH, INCOMPLETE

The extractor binds a figure to a metric without consulting the noun that
governs it:

```python
extract_claims("... Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days; ...")
-> volume_30d_usd  $3,341,200
```

It is a buyback. See `PROJECT_DECISIONS.md` D15 for what this cost.

`agents/reconciliation.py` already carries the fix as three post-filters
(`_binding_is_sound`, `_TRAILING_QUANTITY`, `_DENOMINATOR_PREFIX`,
`_FOREIGN_QUANTITY`, twelve verbatim fixtures). The work is to move them into
`consistency.py` as exported helpers and have `reconciliation.py` import
them — that direction, because `reconciliation` already imports `consistency`
and the reverse is circular.

**Still open, and the part that needs judgement:** whether the cross-report
sweep should apply all three rules. Intra-run compares a handful of agents in
one evaluation and can afford to drop a marginal claim; the cross-report sweep
hunts drift across months and may weigh recall differently. This needs a
corpus measurement, not an assumption.

## Regression bar for whoever resumes

Merged `main` is **339 tests, 6 expected failures**, ruff clean, mypy clean
over 53 files. `backend/tests` is **not** mounted by compose — only
`backend/app` is — so a test-file change needs `docker compose build backend`
or a `-v $(pwd)/backend/tests:/app/tests` mount. A stale image will happily
report an old count.

`reconciliation.py` must not move: GMX `8e4b3c83` → **0 contradictions, 6
uncorroborated**; Aave `c1479a94` → **1 contradiction** rendering `$25.7B` and
`$61.9B` camps. If either changes, the rules changed while being moved.
