# Method, sources and contamination

## The question

Not "was the call right". With zero BUYs, discrimination — did BUYs outperform
PASSes — is uncomputable, and a WATCH is unfalsifiable by construction. The
question graded here is the one from `AIIC_HANDOFF.md` §6.2:

> Did the committee identify the variable that turned out to matter?

A named risk that later drove the price is a HIT. A price driven by something no
agent mentioned is a MISS, and tells us the committee watches the wrong
variables.

## Source 1 — committee reasoning

The `reports` table is empty (`docs/CONTRACTS.md` §2.3) and the 18 June cohort
has no `evaluations` or `agent_outputs` rows at all — it was run by
`concordance_harness.py` against the Orchestrator directly, and
`concordance_results.json` died with the container rebuild on 9 July. The
reasoning survives only in the Notion projects database, written by
`orchestrator._notion_write`.

All six pages were pulled read-only through the running VPS container and saved:

| Project | File | Bytes |
| --- | --- | --- |
| Aave | `source/notion-aave.txt` | 8,909 |
| Plasma | `source/notion-plasma.txt` | 15,808 |
| GEODNET | `source/notion-geodnet.txt` | 9,183 |
| Ethena | `source/notion-ethena.txt` | 9,243 |
| Morpho | `source/notion-morpho.txt` | 9,416 |
| Pendle | `source/notion-pendle.txt` | 9,000 |

Aave and Plasma pages hold two evaluation blocks each; the other four hold one.
`source/verify_scores.py` establishes which block is the graded run.

For Aave alone the full record also exists in Postgres. `agent_outputs` for
evaluation `1a94e47d-68bf-4fdd-a45a-49fb790fbecc` (15 rows, 52 KB) is saved at
`source/pg-aave-1a94e47d-agent-outputs.txt`. This is the only record in the
corpus containing the Committee Chair's decision, its `adjudication_trace`, its
signposts, and Ray's contrarian pass. Everything asserted about Chair behaviour
in this retrospective rests on that one file.

The sibling Aave evaluation `0f48a034-e7a6-45c3-83cb-9a4e1a416692` has 15
`agent_outputs` rows all exactly 323 bytes. Every one is the same payload:

```json
{"error": "Error code: 429 - ... 'type': 'insufficient_quota' ..."}
```

That is the OpenAI quota cascade, and it is why that row is `INSUFFICIENT_DATA`.
Saved at `source/pg-aave-0f48a034-sample.txt`.

### Provenance proof

`source/verify_scores.py` re-implements `orchestrator._calc_score` — the ten
weights, the `technical_analyst` exclusion, the renormalisation over agents that
returned a score — and runs it over the scores parsed out of the Notion text.
Output in `source/score-verification.txt`:

| Project | Ledger `overall_score` | Recomputed from Notion | Δ |
| --- | --- | --- | --- |
| Aave | 77.20 | 77.20 | 0.00 |
| Plasma | 34.30 | 34.29 | −0.01 |
| GEODNET | 62.60 | 62.60 | 0.00 |
| Ethena | 53.20 | 53.18 | −0.02 |
| Morpho | 65.60 | 65.60 | 0.00 |
| Pendle | 62.30 | 62.25 | −0.05 |

All six reproduce to within rounding. The Notion pages are the same runs as the
ledger rows. This matters because the two are joined by nothing — `evaluation_id`
is `NULL` on all eight calibration records (`docs/CONTRACTS.md` §2.4) — so
without this check the corpus would be linked to the ledger by project name and
date alone.

## Source 2 — prices

Seven CoinGecko `market_chart/range` calls, one per asset plus `bitcoin`,
covering 2026-06-10T00:00Z to 2026-08-24T23:59Z. Every response cached to
`source/prices/*-range.json`; 1,809 hourly observations each. Fetch script
`source/prices/fetch.sh`, 20 s between calls, skips anything already cached.
No call was repeated.

### The 429-that-looks-like-200 hazard

CoinGecko's free tier returns **HTTP 200 with a rate-limit body** rather than a
429 status:

```json
{"status":{"error_code":429,"error_message":"You've exceeded the Rate Limit."}}
```

On `/coins/{id}/history` this response has no `market_data` key and is
indistinguishable, to naive code, from "no data for that date". It was observed
firing on the fourth call at 8 s spacing. Every cached file here was checked for
a `status` key and for a populated `prices` array before use; all seven are
clean. This is not merely a note about my own fetches — `docs/CONTRACTS.md` §3.2
mandates that the rewritten `update_checkpoint` use exactly that endpoint, so it
is a live defect risk in unwritten code. See `03-recommendations.md` R1.

### How alpha is computed

```
return_pct = (price_at_checkpoint − entry_price) / entry_price × 100
btc_return_pct = (btc_at_checkpoint − btc_price_at_entry) / btc_price_at_entry × 100
alpha_pct = return_pct − btc_return_pct
```

Simple difference, not ratio — matching the existing
`knowledge/calibration.py` convention so these figures are comparable to
whatever the product eventually writes.

`entry_price_usd` and `btc_price_at_entry` are taken from the live
`calibration_records` table, not re-derived. Dump at
`source/pg-calibration-records.txt`. Entry prices agree with the CoinGecko
series to within 0.1%.

Checkpoint prices are read at **00:00 UTC** of the target date, the convention
`/coins/{id}/history?date=DD-MM-YYYY` uses, so these numbers are directly
comparable to what the fixed endpoint will produce. Computed by
`source/prices/compute.py`; results in `checkpoints-0000utc.json` and
`metrics.json`. Reading end-of-day instead moves Aave's 30-day alpha from
+49.9 to +56.6 — a 7-point convention artefact, which is itself a reason to fix
the convention in code before the 16 September 90-day checkpoint.

## Contamination and limits — read before using any number

**1. Six days at the end dominate everything.** Between 18 and 24 August the
whole market repriced: BTC 64,455 → 77,678 (+21%). Before that, BTC had moved
+2.4% in sixty-one days. Measured at day 61, Ethena's alpha is −12.7 and
Pendle's is −8.2; measured at day 67 they are +49.4 and +8.1. **Two of six
verdicts change sign on the choice of endpoint.** Every figure in
`01-per-project.md` is therefore reported at four dates. Only Aave and Plasma
have alpha signs stable across all four.

**2. This was not a bear market, and the handoff's expectation that it was is
wrong.** §6.2 warns that "in a bear market every PASS/WATCH looks correct on
price". The realised window is the opposite: BTC +21%, five of six assets up.
The bias runs the other way — a PASS looks *expensive*, not safe.

**3. 67 days is not 90 days.** Aave is 73 days from entry; the five-project
cohort is 66–67. The genuine 90-day checkpoint falls on 16 September 2026. No
conclusion here should be restated as a 90-day result.

**4. The 18 June cohort ran on degraded data.** `AIIC_HANDOFF.md` §10 records
CoinGecko 429s across nearly every call in the concordance runs. It is visible in
the text. Plasma's own `onchain_analyst` writes that TVL claims range "from
~$551M to $18.7B across sources with no verified DeFiLlama figure resolvable at
evaluation time"; the failed first run's `tech_infra_analyst` and
`competitive_intel` both assert ~$18.7B TVL while `maturation_scorer` in the
graded run works from ~$551M. GEODNET's `technical_analyst` returned no
technical analysis at all — no candles, no EMAs, no orderbook. **None of this
reduced stated confidence.** Plasma was recorded with `chair_confidence: high`
on inputs that disagreed by a factor of thirty-four. Data quality is not an
input to the confidence field.

**5. Two agents failed silently on Ethena.** `legal_regulatory` and
`devils_advocate` both returned `score: None` with summaries truncated
mid-sentence — they emitted prose before the JSON block and the extractor took
the prose. Renormalisation (§3 of the handoff) then quietly dropped
`legal_regulatory`'s 0.05 weight. Ethena's 53.2 is computed over nine weighted
agents; GEODNET's 62.6 over ten. The ledger records neither fact.

**6. The Chair is missing from five of six records.** `_notion_write` persists
per-agent summaries and a top-five risk list. It does not persist the Chair's
decision, reasoning, signposts, confidence or review date. For GEODNET, Ethena,
Morpho, Pendle and the graded Plasma run, **the reason for the recommendation
does not exist anywhere.** Statements in this retrospective about *why* those
four WATCHes were issued would be invention; none are made.

**7. Driver attribution is journalism, not measurement.** Drivers were
established by dated web search and cross-checked against the price path where
possible (GEODNET's Upbit listing on 27 July matches the series' own maximum on
27 July; Plasma's 28 July unlock matches a slide into a 6 August trough). Where
that cross-check is unavailable the attribution is weaker and is labelled
"driver not established".

**8. n = 6, of which 4 are the same recommendation issued on the same day by
the same batch.** Nothing here supports a claim about the committee's skill.
Everything here is about which *variables* it looks at.
