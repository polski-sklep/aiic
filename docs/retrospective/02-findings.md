# Cross-cutting findings

Every claim below is grounded in `01-per-project.md` and the files under
`source/`. Where n is too small to support a pattern, it says so.

---

## F1 — The committee notices dated supply events, and essentially nothing else with a date

Plasma is the corpus's only clean HIT, and it is the only project where a named
risk carried **a date, a quantity and a direction**: *"1B XPL, July 28 2026,
~39.8% of current circulating supply."* Six agents converged on that one line
independently. It happened on the day, at the size, in the direction stated.

Every other named risk in the corpus is undated and unsigned. They are
conditions, not events: "governance centralization risk", "value disconnect",
"DePIN sector execution risk", "regulatory risk". A condition cannot be graded
and cannot be watched, because there is no moment at which it resolves.

This is the single strongest positive result: **when the committee is forced by
the subject matter to produce a date and a magnitude, it gets it right.** The
capability is present. Nothing in the output schema asks for it.

## F2 — The committee's tokenomics frame counts dilution and does not model demand

Four of six projects turned on a value-accrual mechanism, and the committee got
all four wrong in the same direction:

| Project | Value-accrual variable | How the committee framed it | What happened |
| --- | --- | --- | --- |
| Aave | Aavenomics 3.0 buyback | not mentioned; "Aave Will Win" analysed only as governance capture | activated 27 June, ~$400M/yr into open-market buys |
| Ethena | fee switch | risk #1 = *risk it never activates* | thresholds met; revenue ~$61M in August |
| Morpho | fee switch | risk #1 = *risk it never activates*, "permanently hollow" | still off; token +23.5 alpha anyway |
| GEODNET | 80% buyback-burn | "net inflation/deflation balance unverifiable"; "mostly aspirational" | net-deflationary from 1 August |

The asymmetry is structural, not incidental. Where supply is concerned the
tokenomics agent produces numbers, dates and percentages of float. Where demand
is concerned it produces a *risk that the demand mechanism fails to arrive*.
There is no scenario in any of the six reports in which a value-accrual
mechanism activates and the price responds. The upside branch is not modelled.

This is also the mechanism behind the Aave miss. `tokenomics_analyst` scored Aave
85 and never mentioned Aave Will Win; `governance_analyst` scored it 65 and
discussed AWW exclusively as a legitimacy crisis. The same event, two agents, two
frames, and no one holding both.

Two things about that gap, and they pull in different directions.

It is **not** an argument against data-agent independence
(`docs/CONTRACTS.md` §4.2), because the information was not lost. It reached the
synthesis layer intact, and the Devil's Advocate — which sees everything —
explicitly stated the buyback thesis and rejected it. The failure is at
adjudication, not at collection.

But the personas were not describing an independent layer at all. Six of the
eight data-agent `INTERFACES.md` files declare "Receives From" lists naming
sibling data agents and list sibling outputs under "Optional Inputs" — Economics
is told to expect "Governance analysis" that the runtime can never deliver
(`00-method.md`, contamination 10). So the agent that owned value accrual was
told governance context was an available input, while the agent holding the Aave
Will Win facts had no path to it and no reason to think one was needed. That is a
documented persona/runtime mismatch, live on this exact run. It is a **candidate
mechanism, not a proven cause** — nothing in the outputs shows an agent declining
to chase a fact because it expected a colleague to supply it. It is the first
thing to rule out, and it is cheap to rule out: correct the interface files and
see whether the frame gap survives.

## F3 — Liquidity and distribution are treated as risk-to-us, never as catalyst

The committee has no concept of *who will be able to buy this next*.

- GEODNET: `technical_analyst` reported it could produce no analysis because GEOD
  is not on Binance, and concluded that "limited exchange presence significantly
  elevate[s] execution risk". An Upbit listing on 27 July then produced the
  largest single move in the entire cohort (+54.6%). Same fact, unexamined sign.
- Ethena: the proximate trigger was a $1B FalconX institutional warehouse
  facility — a distribution event. No agent tracks partnerships or credit lines.
- Aave: Standard Chartered initiating coverage (25 June) and a reported Kraken
  15% stake (25 June) both landed inside the window. Neither class of event is
  anyone's beat.

This is partly a persona gap and partly a tool gap, and the tool gap is the real
one. The eleven registered tools (`docs/CONTRACTS.md` §3.6) cover price, token
info, TVL, protocol fees, klines, orderbook depth, technical levels, web search,
Twitter and Notion. Nothing enumerates exchange listings, listing roadmaps,
institutional partnerships or coverage initiations. `web_search` can find them,
but only if an agent thinks to ask, and no persona instructs one to.

## F4 — Macro and regulation are modelled as risk only

`field_intel` scored 38, 42, 42, 28, 65, 75 across the corpus — mean deviation
**−17.0 points below the composite it feeds**, by far the most negative of any
weighted agent (`source/score-verification.txt`). On all five June-2026 runs it
tells the same story: hawkish Warsh FOMC, dot plot up, hostile to altcoins.

The largest price event in the measurement window was macro-regulatory and
positive: the US Treasury doubling long-end buybacks on 19 August, the SEC's
402-page *Regulation Crypto Assets* proposal on 18 August, a White House crypto
summit and CLARITY Act push on 19 August, and $1.44B of shorts liquidated. BTC
+21% in six days.

Nobody could have forecast the dates. That is not the finding. The finding is
that **the committee's two macro-facing agents have no upside branch at all.**
`legal_regulatory` produces structural descriptions — entity, jurisdiction,
enforcement status — and never a forward catalyst. `field_intel` produces a
one-directional headwind narrative. Across six reports there is exactly one
instance of macro framed as a tailwind (Plasma's GENIUS Act mention), and it is
immediately discounted as benefiting "the sector broadly rather than Plasma's
token specifically."

A committee that can only detect downside in the two variable classes that moved
this market by 21% in a week will systematically underrate assets.

## F5 — The "top five deduplicated risks" is one agent's list

This is a defect in `orchestrator._notion_write`, and because Notion is the only
surviving record it has already cost this project its corpus.

| Project | Attribution of the five surviving risks |
| --- | --- |
| Aave | 3 × `tokenomics_analyst`, 2 × `governance_analyst` |
| Plasma | 5 × `tokenomics_analyst` |
| GEODNET | 5 × `tokenomics_analyst` |
| Ethena | 5 × `tokenomics_analyst` |
| Morpho | 5 × `tokenomics_analyst` |
| Pendle | 5 × `tokenomics_analyst` |

Twenty-five of thirty surviving risk lines come from one agent of fifteen. This
is not deduplication; it is truncation of an unsorted list that happens to start
with the first agent in the roster. The Risk Officer's risk array, Ray's risk
array and the Devil's Advocate's ten-item challenge list are all discarded at
this stage.

The consequence for this retrospective was direct: F1's finding that "only
Plasma's risks carried a date" is measured over a sample that is 83%
`tokenomics_analyst`. It happens that the full agent summaries survive alongside
the list, so the analysis was still possible — but the artefact designed to be
the durable record preserved one agent's view.

## F6 — Named risks carry no date, no direction and no expiry, so some of them are simply stale

- **Morpho, risk #2**: *"October 2025 Cohort 2 unlock: 168M tokens… creates
  immediate, material sell pressure"* — written on 18 June 2026, about an event
  eight months in the past, in the present tense.
- **Ethena, risk #2**: cites the "post-April 2025 cliff expiry", fourteen months
  prior, as an active driver.
- **Pendle, risk #3**: *"TVL showed a notable single-day drop from ~$1.18B to
  ~$960M in the 7-day trend"* — a one-week price-of-capital wiggle promoted to a
  top-five structural risk.

A risk list that mixes live dated catalysts, historical facts and last week's
noise cannot be scored later, cannot be watched, and cannot generate a signpost.
It is the root cause of F1: the schema does not ask "by when" or "which way", so
most of the time the agents do not say.

## F7 — Agent scores have large, stable, measurable biases, and the heaviest-weighted agent has no persona

From `source/score-verification.txt` — mean deviation of each agent's score from
the composite it feeds, n = 6 (n = 5 where the agent failed once):

| Agent | Mean dev | Weight |
| --- | ---: | ---: |
| `maturation_scorer` | +8.0 | 0.10 |
| **`tech_infra_analyst`** | **+7.3** | **0.15** |
| `legal_regulatory` | +5.8 | 0.05 |
| `competitive_intel` | +3.1 | 0.10 |
| `tokenomics_analyst` | +2.1 | 0.15 |
| `governance_analyst` | −1.4 | 0.08 |
| `onchain_analyst` | −2.5 | 0.12 |
| `portfolio_manager` | −5.2 | 0.05 |
| `risk_officer` | −8.4 | 0.15 |
| `field_intel` | −17.0 | 0.05 |
| `devils_advocate` | −27.0 | unweighted |

Two things follow.

`tech_infra_analyst` carries the joint-highest weight (0.15) and is the
second-most optimistic agent in the committee — above the composite on five of
six projects. It is also, per `docs/CONTRACTS.md` §2.1, **absent from
`AGENT_FOLDERS` and running with no persona at all**: `load_agent_persona`
returns `""` and `BaseAgent` falls back to `role_description`. A 15% weight on
an unconstrained generic prompt is the most likely explanation for the bias, and
it is the cheapest thing in this document to fix.

`technical_analyst` is the same defect one folder over. `technical-analyst/`
holds a single `SOUL.md` against four to six files for every peer and has no
`INTERFACES.md`. Its scores across the corpus are 55, 28, 28, 32, 28, 44 —
uniformly low, and on GEODNET it returned no analysis at all. It is excluded from
scoring (`docs/CONTRACTS.md` §4.1), so this never touches the composite, but its
output *is* piped to the Chair as `technical_entry_context`. What reaches the
Chair on entry timing is thin because the persona behind it is thin.

`devils_advocate` sits 27 points below the composite by design and carries zero
weight — it is a rhetorical device, not an input. Which matters because of F8.

## F8 — Nothing that is wrong ever costs anything

The Devil's Advocate produced the single most consequential error in the corpus:
it named the Aave buyback thesis, dismissed it on a historical prior ("see UNI")
and a political-impossibility claim, and both halves were falsified sixteen days
later. It scored Aave 25/100. There is no mechanism anywhere in the system that
will ever notice.

The same is true of the Chair's five Aave signposts — none fired, the price
doubled, and the signposts are stored only in `agent_outputs`, unreferenced by
the calibration ledger. And of `field_intel`'s uniformly bearish June calls.

The ledger stores a recommendation, a score and a confidence string. It stores
no claim, no signpost, no date and no counter-argument. Calibration against it
can therefore only ever measure the one thing the corpus cannot support —
discrimination between buckets — and can never measure the thing it can: whether
the committee named the variable.

## F9 — Data quality does not reach the confidence field

`AIIC_HANDOFF.md` §10 records CoinGecko 429s across nearly every call in the
concordance runs. The damage is legible in the text:

- Plasma's `onchain_analyst`: *"TVL claims range from ~$551M to $18.7B across
  sources with no verified DeFiLlama figure resolvable at evaluation time"* —
  a factor of 34, acknowledged in-line.
- The failed first Plasma run has `tech_infra_analyst` and `competitive_intel`
  both asserting ~$18.7B TVL while the graded run works from ~$551M.
- GEODNET's `technical_analyst` returned no technical analysis whatsoever — no
  candles, EMAs, RSI, ATR or orderbook — and scored 28 on the absence.
- Ethena's `legal_regulatory` and `devils_advocate` returned `score: None` with
  summaries truncated mid-sentence. Renormalisation silently dropped
  `legal_regulatory`'s 0.05 weight; the ledger records neither the failure nor
  the reduced denominator.

Plasma was nonetheless recorded with `chair_confidence: high`. **Confidence is a
judgment about the thesis and is entirely decoupled from the quality of the
inputs.** For a system whose purpose is calibration, that is close to fatal: a
high-confidence call made on contradictory data and a high-confidence call made
on clean data are indistinguishable in the ledger.

Bearing on the exercise: yes, the committee was reasoning on thin data, and
that is a mitigating fact for GEODNET and Plasma. It is *not* a mitigating fact
for Aave, which ran through the API on 11 June with all fifteen agents returning
full outputs.

## F10 — The Chair decided before the score existed, and read 70% of the report

Verified in the tree at the base commit, so true for all six evaluations.

**The score does not exist when the Chair decides.** `orchestrator.py` runs the
Chair, and only on the following lines builds the `scores` dict and computes
`overall = self._calc_score(agent_results)`. The Chair's own score is parsed out
of its output and then plays no part in the recommendation. So `overall_score`
is not an input the Chair overrides — it is a number computed *afterwards*, from
agents the Chair had already read individually, and then stored in the ledger
beside the Chair's decision as though the two were commensurable. They never
met.

**The report reaches the Chair truncated by a raw character slice.** `chair.py`
line 34:

```python
report_text = json.dumps(report, indent=2, default=str)[:6000] if report else "No report available"
```

On the Aave run the serialised report is 8,562 characters. The Chair saw 70% of
it and lost six of twenty-four sections: `8_community_sentiment`,
`12_maturation_analysis`, **`24_signposts_to_monitor`**,
`7_competitive_landscape`, `6_technical_architecture` and
`15_investment_thesis_alignment`.

**Which six are lost is decided by the language model's key emission order, and
is therefore non-deterministic and unlogged.** On this run the writer emitted
sections in a scrambled order that happened to place `22_overall_score` at
character 2,446 — so the score and recommendation survived, and the Chair
correctly recorded `report_writer_recommendation: "WATCH"`. Re-serialise the
same report in the numeric order its own key names imply and the cut falls
between sections 16 and 17, taking the bear case, key risks, key opportunities,
mandate compliance, score breakdown, overall score, recommendation and signposts
with it. Same report, same code, eight sections of difference, decided by which
order the model felt like emitting keys in.

This corrects a claim that would otherwise be tempting: the truncation did *not*
cause the Aave outcome. The Chair saw section 22 and quoted it. But the Chair
also lost the competitive landscape section on a case whose loudest bear
argument was Morpho competition, and lost the report writer's signposts before
writing its own. And on any run where the writer emits in numeric order, the
Chair adjudicates having never seen the score, the recommendation or the risk
list.

Both defects are cheap to fix and neither requires deciding anything about
conviction: compute the score before the Chair runs and pass it in; truncate the
report by dropping whole sections in a declared priority order, and log which
were dropped.

---

## The §6.5 structural finding — held up, with one correction

`AIIC_HANDOFF.md` §6.5 asserts four things. Against the six reports:

**"The committee has no conviction mechanism. It can veto but cannot convict."
— CONFIRMED.** Zero BUYs, zero VETOs in eight records. The Risk Officer's output
shape is a gate: `veto: false`, `veto_reason: null`, plus a
`veto_triggers_checked` map of six boolean conditions. On Aave all six evaluate
false with evidence attached; the agent then still scores 64 and flags a "watch
downgrade trigger". There is no symmetric object anywhere in the pipeline — no
agent can raise conviction, only lower it. The `portfolio_manager` comes closest
and its own language is *"a small starter or watch placement is the appropriate
fit"*, *"a modest, patiently-sized initial position"*. Nothing in fifteen agents
argues for size.

**"WATCH is free and unfalsifiable." — CONFIRMED, and worse than stated.** Four
of six are WATCH. But for all four, plus the graded Plasma run, **the Chair's
reasoning does not exist anywhere.** `_notion_write` persists per-agent summaries
and the top-five risks; it does not persist the Chair's decision, reasoning,
confidence, signposts or review date, and the 18 June cohort has no
`agent_outputs` rows. So a WATCH is not merely unfalsifiable — it is unauditable.
We cannot say why GEODNET was watched. We can only say what its analysts thought.

**"Its default output is a medium-confidence non-decision." — CONFIRMED.** Four
of six WATCH, all at `medium`, scores 53.2–65.6, clustered on the 60–74 band
boundary. The two exceptions are both `high`-confidence PASSes at the extremes
(77.2 and 34.3). The committee is decisive only when rejecting.

**"One high-confidence judgment contradicted its own scoring (Aave)." —
PARTIALLY REFUTED. Correct at the ledger, wrong about the Chair.** The handoff
§3.1 frames this as a computed number being silently overridden by a judgment
agent. That is not what happened, and F10 gives the mechanism: **the 77.2 did
not exist yet.** `_calc_score` runs *after* the Chair. What the Chair had was the
report writer's 73.5, which it read (section 22 survived the truncation at
character 2,446) and acted on. It produced its own score of **73.5**, recorded
`report_writer_recommendation: "WATCH"`, `risk_officer_approved_override: true`,
an explicit `threshold_crossed`, an `override_reasoning`, and separate
`objections_judged_fatal` and `objections_judged_non_fatal` lists. This is a
documented adjudication against a hard mandate exclusion, not an arbitrary
override.

The real defect is one layer down: **that trace is written to `agent_outputs` and
to nothing else.** `calibration_records` stores `overall_score = 77.20`,
`recommendation = PASS`, `chair_confidence = high`, and `evaluation_id = NULL`.
From the ledger — the only artefact calibration reads — the contradiction is
invisible, the reasoning is unreachable, and the mandate rule that caused it is
unrecorded. And for the five records that did not run through the API, the trace
does not exist at all.

So §6.5's conclusion stands and is if anything understated. Its diagnosis of the
Aave case needs correcting on two counts before it drives a design decision.
The problem is not that the Chair overrides a score without reason — there was
no score to override, and the reason was recorded in full. The problems are that
the system computes a number after the fact, stores it in the ledger beside a
decision it never informed, and then throws the reasoning away.

That distinction matters for what gets built. "Replace cardinal scoring with
ordinal conviction tiers" is a response to a Chair that ignores its own score.
This Chair never received one. Sequencing the score before the Chair (F10) and
persisting the trace (R2) are prerequisites to knowing whether the cardinal
score is even the problem — and both are cheap, whereas redesigning decision
semantics is not.
