# `agent/consistency-audit` — findings

Resumed 2026-08-26 14:40 from the pause note. Branch `agent/consistency-audit`,
worktree `worktrees/consistency-audit`. Two commits: `b91204b` (inherited,
unverified at handover) and `afd67a0` (this session). Nothing merged, nothing
pushed, production untouched — every database access was a `SELECT` or a
read-only `COPY … TO STDOUT` against `committee-postgres` on the VPS.

Supersedes `docs/PAUSED-consistency-audit.md`, which is on `main` and which this
branch never carried. Delete it when this merges.

---

## 1. Does `b91204b` hold up? Yes — reproduced both halves

The pause note said the fix was committed but that no agent had ever reported
evidence for it. Ran all four cases in the container against the live module and
against `git show b91204b^:…/consistency.py`.

**The crash is real and the corpus in the note reproduces it exactly.**

```
$ python3 repro_task1.py            # pre-fix module, the two-claim corpus
Traceback (most recent call last):
  File "/scratch/prefix/consistency_prefix.py", line 983, in detect_conflicts
    period=" vs ".join(sorted(periods)),
                              ^^^^^^^
UnboundLocalError: cannot access local variable 'periods' where it is not
associated with a value
```

**The leak is worse than the crash, and worse than the commit message says.**
Pre-fix, with a value conflict emitted first, the date-attribution finding
inherits the *other bucket's* period — and in this fixture that bucket belongs to
a **different entity**:

```
[value]     Aave        perp_market_share_pct  period='2026-03'
[date-attr] Hyperliquid perp_market_share_pct  period='2026-03'   <-- Aave's
    its own claims' periods: ['2026-01', '2026-06']
    note: "The same figure (44%) is dated to 2026-03 by different evaluations."
```

The note is self-refuting: it says one figure is dated to *one* period by
different evaluations.

**Fixed path produces a correct finding.**

```
[date-attr] Hyperliquid perp_market_share_pct period='2026-01 vs 2026-06'
            severity=high  spread=0.0
    its own claims' periods: ['2026-01', '2026-06']
    note: "The same figure (44%) is dated to 2026-01 and 2026-06 by different
           evaluations. At most one dating can be right; a signpost built on
           either cannot fire correctly."

fingerprint stable alone vs crowded: True
every rendered period is asserted by one of the finding's own claims: True
```

Right `period`, right `note`, stable fingerprint. The commit message quotes
fingerprints `5cdeca87…`/`5df2e23d…`; mine are `ea51afa8…`/`1f3b80e4…` because my
fixture's `quote` text differs. The property that matters — one logical finding,
one identity, independent of what else is in the corpus — is what I verified, and
it holds.

**Ledger impact re-verified independently:** `consistency_findings` 0 rows,
`consistency_audit_runs` 0 rows in production. No persisted fingerprint is
orphaned.

`b91204b` stands. It is now covered by eight tests in `test_consistency.py`.

---

## 2. The extractor fix, moved

`_binding_is_sound`, `_TRAILING_QUANTITY`, `_DENOMINATOR_PREFIX`,
`_FOREIGN_QUANTITY` and `_BACKWARD_CLAUSE_BREAK` left
`backend/app/agents/reconciliation.py` and are now
`binding_is_sound`, `TRAILING_QUANTITY`, `DENOMINATOR_PREFIX`,
`FOREIGN_QUANTITY`, `BACKWARD_CLAUSE_BREAK` in
`backend/app/knowledge/consistency.py`, with the four private helpers
(`_locate_value`, `_restates_a_preceding_quantity`, `_own_metric_bindings`,
`_nearest_rival`). `reconciliation` imports them; the reverse would be circular.

The sweep applies them inside `extract_claims`, **after** `_drop_comparatives`,
because that is the position `reconciliation` applied them in. Filtering earlier
would change what `_drop_comparatives` sees. `reconciliation` still calls
`binding_is_sound(claim, reject_backward_reach=True)` on the survivors; since the
sweep's rule set is a strict subset, the composition is unchanged — measured, not
assumed, in §4.

---

## 3. Which rules the sweep applies, and the measurement for each

Both corpora, measured 2026-08-26 against the live database. The sweep corpus is
the 11 rows `_CORPUS_SQL` actually returns (2 from `reports`, 9 from
`agent_outputs.report_writer`; 5 report_writer rows have a null `sections` and
`Ethereum Name Service` has no outputs at all). The within-run corpus is all 15
agents' prose across 16 evaluations.

|   | rule | sweep | within-run | applied cross-report? |
|---|---|---|---|---|
| 1 | bracket restates a preceding quantity | **3** | 13 | **yes** |
| 2 | nearer term names a different quantity | **0** | 2 | **yes** |
| 3 | "…% of `<metric>`" is a denominator | **0** | 1 | **yes** |
| 4 | label reaches backwards over a clause break | **1** | 1 | **no** |

Pre-filter claim counts: 36 (sweep), 161 (within-run).

### Rule 4 — not applied, and the evidence says delete it, not weaken it

It fires **once on each corpus, both times on the same sentence**:

> `On market cap, Hyperliquid is ~$18.3B vs GMX $75M [maturation_scorer].`
> — GMX `8e4b3c83`, §7 competitive landscape

That claim is true and correctly bound. It is the exact adjacency
`_METRIC_WINDOW`'s own docstring cites as legitimate. **Rule 4 has no measured
true positive on either corpus.** The sentence it was written for —
"GMX is in a daily uptrend, trading at $7.20" — is already removed by rule 2 via
`FOREIGN_QUANTITY`, which I confirmed by ablation.

Cross-report, the cost is concrete: each report speaks once, so a deleted
Hyperliquid market-cap claim is the only evidence of drift there could be.
Within a run, fifteen agents restate the same facts and D15 gates a finding on
two of them agreeing, so the loss is cheap — that is why it stays enabled there.
It stays enabled mainly because `reconciliation`'s measured behaviour is pinned
and is not this branch's to move. If anyone revisits it, the measurement says
remove it, not port it.

### Rules 2 and 3 — applied, though they never fire cross-report

Zero firings on the sweep corpus. Kept anyway, for two reasons. First, the two
callers must share one definition; the whole point of the move is that two copies
cannot drift. Second, both are precision-only — they can remove a claim, never
create or misattribute one — and both remove real defects on the within-run
corpus:

```
[rule 2] "NEXT UNLOCK: ~14.175M HYPE tokens unlock August 29, 2026 (4 days),
          representing 1.4% of total supply and ~2.7% of current market cap at
          current prices (~$1.16B notional)."
          -> extractor said Hyperliquid market_cap_usd = ~$1.16B. It is 2.7% of it.

[rule 2] "GMX is in a daily uptrend, trading at $7.20 — above all three major
          EMAs (20/50/200)…"
          -> extractor said GMX volume_24h_usd = $7.20. It is the share price.

[rule 3] "Next confirmed unlock: ~14.18M HYPE … representing ~1.4% of total
          supply and ~2.7% of market cap (~$1.16B at current price)."
          -> same $1.16B defect, reached through "% of" rather than a rival noun.
```

### Rule 1 — applied; two correct drops and one real cost

All three sweep firings, read at source:

**Correct — GMX `8e4b3c83`, §5 on-chain metrics.** D15's case.
> `Buybacks: 103,764 GMX ($3,341,200) purchased over 30 days;`
> Bound to `volume_30d_usd`. It is a buyback. This is the mis-binding that was
> read as an 840x contradiction against the same report's ~$2.8B.

**Correct — Aave `1a94e47d`, §24 signposts to monitor.**
> `["Resolution of governance concentration below 60% threshold", …, "Morpho`
> `competitive threat - monitor TVL share changes", …, "GHO stablecoin adoption`
> `reaching $1B market cap", …]`
> Bound to `Morpho market_cap_usd = $1B`. The section is a JSON list, the whole
> list parses as one sentence, and nearest-preceding-entity attribution picks
> "Morpho" for a figure that belongs to GHO. It is also a *target*, not a fact.
> The drop is right; note that rule 1 reaches it through the list's own `[`,
> which is a coincidence rather than the rule's intent.

**A cost — Hyperliquid `be8210d4`, §7 competitive landscape.**
> `Named rivals with numbers: Aster (~20.9% share, $15B+ daily at peak);`
> `$15B+ daily at peak` really is Aster's 24-hour volume, and the claim is
> substantively true. Rule 1 removes it because `~20.9%` sits in front of it
> inside the same bracket.
>
> Tolerable for two reasons, neither of which is the rule's stated one: the
> figure is peak-qualified and the sweep cannot tell a peak from a current
> reading, so it would eventually read as drift; and Aster is named once in
> eleven reports, so the claim is in no finding. Pinned in
> `test_the_measured_recall_cost_of_the_bracket_rule`, worded as a cost so a
> future change that restores the claim reads as an improvement rather than a
> regression.

---

## 4. Corpus before/after

### Cross-report sweep

| | claims | conflicts |
|---|---|---|
| no binding rules (= `main`'s sweep) | 36 | 4 |
| rules 1–3 (**shipped**) | 33 | 4 |
| rules 1–4 | 32 | 4 |

**The four findings are identical in all three configurations, same
fingerprints** — `774594ed`, `63326d11`, `c03d4aa0`, `2d5acda1`. Every claim any
rule removes was in no finding. Stated plainly: on today's corpus this change
buys precision that has not yet been exercised. It removes three wrong or
unusable claims before they can collide with a twelfth report, and it does not
change anything the committee would see today.

The four standing findings, unchanged, all Hyperliquid, all `be8210d4` against
`8e4b3c83`: perp market share `~44%` vs `70-80%+` (high), perp market share
`36.4%` vs `44%` for 2026-01 (medium), 30-day volume `~$172B` vs `~$245B`
(medium), and the date attribution of `44%` across 2026-01 / 2026-mid / 2026-08
(high).

### Within-run reconciliation — unmoved, byte for byte

`reconcile_data` run over the real `agent_outputs` of all 16 persisted
evaluations, at `HEAD` (`b91204b`, rules local to `reconciliation`) and at the
working tree (rules imported):

```
$ diff recon_head.json recon_wt.json && echo IDENTICAL
IDENTICAL
$ md5 -q recon_head.json recon_wt.json
7f63cbd5aa68f2704c3b1060855dc7e3
7f63cbd5aa68f2704c3b1060855dc7e3
```

Both named invariants hold exactly:

```
GMX          8e4b3c83  claims= 61 agents=11 contradictions=0 uncorroborated=6
Aave         c1479a94  claims= 10 agents= 8 contradictions=1 uncorroborated=0
```

Aave's one contradiction renders the two camps it should:

```
tvl_usd, 2026-04, ratio 2.4
  $25.7B  committee_chair, competitive_intel, onchain_analyst, report_writer,
          tokenomics_analyst
          "…dominant market position ($25.7B TVL, 62% market share)…"
  $61.9B  governance_analyst, maturation_scorer, risk_officer
          "…successfully building ETHLend into Aave with $61.9B TVL across 20+ chains"
```

`prose_claims_extracted` is non-zero on both sides, which matters: `reconcile_data`
swallows any prose-pass exception and degrades to zero contradictions, so "0
contradictions" alone would not have distinguished a working check from a broken
import.

---

## 5. Verification output

```
$ docker compose -p aiic-audit run --rm --no-deps backend \
      python3 -m unittest discover -s tests
Ran 355 tests in 0.517s
OK (expected failures=6)
```

Baseline re-measured the same way, from `git archive main` (`6192412`), not
assumed:

```
Ran 339 tests in 0.577s
OK (expected failures=6)
```

+16 tests, no regressions. `backend/tests` is bind-mounted in this worktree's
`docker-compose.override.yml`, so no stale image is involved.

```
$ docker run --rm -v "$PWD":/src -w /src aiic-audit-dev ruff check .
All checks passed!

$ docker run --rm -v "$PWD":/src -w /src aiic-audit-dev mypy
Success: no issues found in 53 source files

$ docker compose -p aiic-audit run --rm --no-deps backend \
      python3 -c "import app.main; print('IMPORT OK')"
IMPORT OK
```

`ruff format --check` reports 60 files needing reformatting — identical on `main`
and on this branch, including all four files touched here. It is pre-existing and
not part of the regression bar; nothing was reformatted.

Local stack `aiic-audit` torn down, volumes removed.

---

## 6. Left for someone else

- **`backend/app/agents/orchestrator.py:803`** carries a comment naming
  `_binding_is_sound`, which no longer exists under that name. One word, but
  `orchestrator.py` belongs to `agent/persistence` / `agent/core` under
  CONTRACTS §1, so it is reported rather than fixed. New name:
  `consistency.binding_is_sound`.
- **`backend/tests/**` belongs to `agent/qa`** under CONTRACTS §1. This branch
  edits `test_consistency.py` and `test_reconciliation.py` because `b91204b`
  already did and the work is untestable otherwise. Flagged, not hidden.
- **Rule 4** should probably be deleted from the within-run check too — no
  measured true positive on either corpus. Out of scope here: it would move
  `reconciliation`'s pinned behaviour, which this branch is not allowed to do.
- **A JSON list parses as one sentence.** The Aave signposts case is only caught
  because a stray digit preceded the figure inside the list's own bracket.
  Nearest-preceding-entity attribution over a flat list of unrelated strings is
  unsound in general, and nothing currently guards it.

The branch is ready. Not merged, not pushed.
