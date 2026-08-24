# Performance retrospective — 11 & 18 June 2026 cohort

**Written 24 August 2026.** Corpus: the six usable rows of `calibration_records`
(`docs/CONTRACTS.md` §2.6) and the committee reasoning behind them.

## What this corpus can and cannot support

Six records — two PASS, four WATCH, zero BUY, zero VETO — evaluated 66 to 73 days
ago, four of them produced by a single batch on one afternoon. It cannot measure
skill: the scorecard's key metric is discrimination between BUYs and PASSes, and
with zero BUYs that is uncomputable rather than merely weak. It cannot be read as
a 90-day result; the genuine 90-day checkpoint falls on 16 September 2026. It
cannot be read off raw return, and it cannot be read off a single date either —
two of the six alpha figures change sign between 18 and 24 August, because the
whole market repriced +21% in the final six days of the window. What it *can*
support, and all this document attempts, is the §6.2 question: for each project,
did the committee name the variable that turned out to drive the price? That is
gradeable at n = 6 and it is where the actionable findings are.

## Documents

| | |
| --- | --- |
| [`00-method.md`](00-method.md) | Sources, provenance proof, how alpha is computed, and eight contaminations to read before using any number |
| [`01-per-project.md`](01-per-project.md) | The core: named risks vs. realised drivers, one section per project, with a HIT/MISS/PARTIAL/UNRESOLVED verdict |
| [`02-findings.md`](02-findings.md) | Nine cross-cutting patterns, plus the assessment of the handoff's §6.5 structural finding |
| [`03-recommendations.md`](03-recommendations.md) | Nine ranked recommendations naming specific artifacts, three speculative items, and what could not be established |

## Results at a glance

| Project | Rec | Score | α @30d | α @67d | Verdict |
| --- | --- | ---: | ---: | ---: | --- |
| Aave | PASS | 77.2 | +49.9 | +100.1 | MISS |
| Plasma | PASS | 34.3 | −19.6 | −26.4 | HIT |
| GEODNET | WATCH | 62.6 | −7.6 | −13.0 | PARTIAL |
| Ethena | WATCH | 53.2 | −13.1 | +49.4 | PARTIAL |
| Morpho | WATCH | 65.6 | −1.2 | +23.5 | MISS |
| Pendle | WATCH | 62.3 | +7.6 | +8.1 | UNRESOLVED |

α = asset return − BTC return from entry. BTC moved +1.5 to +1.8% over the
30-day windows and +21.4% over the 67-day window; the second column is mostly a
six-day market event and should not be read on its own.

## `source/` — the raw corpus, saved so it cannot be lost again

The reasoning behind these six decisions existed in exactly one place: the Notion
projects database. The `reports` table is empty and the 18 June cohort has no
`evaluations` or `agent_outputs` rows — it was run by a harness that bypassed the
API, and its results file died with the container rebuild on 9 July. Everything
under `source/` was pulled read-only from the running VPS and committed here so
that this analysis is reproducible and the corpus survives the next rebuild.

```
source/notion-{aave,plasma,geodnet,ethena,morpho,pendle}.txt   committee reasoning, ~9 KB each
source/pg-aave-1a94e47d-agent-outputs.txt                      the only full 15-agent record, incl. the Chair
source/pg-aave-0f48a034-sample.txt                             the OpenAI-quota failure that produced INSUFFICIENT_DATA
source/pg-calibration-records.txt                              the live ledger, as read
source/pg-evaluations-index.txt                                13 evaluations, 30 agent_output rows
source/verify_scores.py, score-verification.txt                reproduces all six ledger scores from the Notion text
source/prices/*-range.json                                     7 × 1,809 hourly CoinGecko observations
source/prices/{fetch.sh,compute.py}                            how they were fetched and reduced
source/prices/checkpoints-0000utc.json, metrics.json                    every figure quoted above
```

Nothing was written to Notion, to Postgres or to the VPS.
