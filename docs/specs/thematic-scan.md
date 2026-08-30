# Thematic scan — specification

Written 30 Aug 2026 against the 15-report production corpus. **Nothing here is
built.** This is the argument for what to build, and the measurements that
argument rests on.

Every number below was measured by querying production read-only on 29–30 Aug
2026. Where a number could not be measured at this corpus size, it says so
rather than proposing a threshold nobody calibrated.

---

## The gap

`knowledge/consistency.py` is a **numeric contradiction detector**: same entity,
same metric, same period, incompatible values. All four of its live findings are
Hyperliquid figure disputes. It is working as designed.

Jacob's observation is a different object:

> *"I'm observing that value accrual issues exist in both Arbitrum and Plasma."*

There is no contradiction here. Both reports are internally consistent; they
simply say something structurally similar about unrelated protocols. The sweep
cannot surface this and no amount of tuning would make it. The thing being asked
for is a **cross-project thematic pattern**, and the deliverable is one holistic
observations report: what does the committee keep concluding across the corpus?

---

## Verdict up front

| Question | Answer |
|---|---|
| Does Jacob's Arbitrum/Plasma example hold up? | **Yes**, and it survives the artefact test — but the naive version of the test is worthless (see below) |
| What is a theme, mechanically? | Structured extraction (free) + LLM label over **extracted risk items only**, not embeddings, not a phrase lexicon |
| Can persona artefacts be separated from real patterns? | **Yes**, by per-agent contribution — and this is the most valuable output of the whole feature |
| What is worth reporting? | 3 of 4 proposed criteria calibrate at n=15; **the decision-correlation criterion does not, and is recorded rather than gated on** |
| Cost | **~$0.12 per run, ~$0.12/month.** The discriminator itself is free |
| Where does the output go? | **A human, and nothing else.** Endpoint + Telegram, mirroring the numeric sweep. This is an invariant, not a preference |
| Same driver as the sweep? | Same driver, same tick, separate policy and separate module |

---

## Testing the premise before building anything

D15 exists because a check was commissioned against a contradiction that did not
exist — the GMX `$3,341,200` "volume" was a buyback figure, and reading the
source text was the step that had been skipped. So the source text comes first.

**Arbitrum `40eaf3d8`, section `3_tokenomics`:**

> "Value accrual is the central weakness and the sharpest committee
> disagreement. Live mechanism today: NONE — holders vote but receive zero fee
> share, staking yield, or burn"

**Arbitrum `40eaf3d8`, section `18_key_risks`:**

> "Structural: ARB has zero direct value accrual — 100% of sequencer, Timeboost
> and AEP revenue flows to the DAO treasury, none to holders."

**Plasma `fb190612`, section `3_tokenomics`:**

> "The fatal tension, flagged by tokenomics_analyst, onchain_analyst and
> devils_advocate alike: zero-fee transfers mean near-zero base fees to bu[rn]"

**Plasma `fb190612`, section `18_key_risks`:**

> "Structural: value-accrual contradiction — zero-fee UX gives near-zero base
> fees to burn while validator inflation runs 3% (floor) to 5% with no supply
> cap ($26M–$43M/yr), making XPL net-inflationary indefinitely"

**The premise holds.** Both reports raise value accrual as a structural, named,
top-tier weakness, in a chain and a rollup that share no architecture, no
category and no team. That is a real cross-project pattern and it is worth
surfacing.

Two things were learned in the course of confirming it, and both change the
design.

### The naive test is worthless

**14 of the 15 reports in the corpus mention value accrual.** The only one that
does not is the older Plasma evaluation `d5571fd9` from April.

So "value accrual is weak in Arbitrum and Plasma" is, as a *detection*, almost
information-free — a detector keyed on the phrase would have flagged 11 of 12
distinct projects and told Jacob nothing he could act on. Whatever makes his
observation interesting, it is not that the words appear.

### The phrase match is lexically fragile

Plasma writes **"value-accrual"** with a hyphen. Arbitrum writes **"value
accrual"** with a space. Matching the spaced form against `18_key_risks` finds
6 of 15 reports; matching `value[ -]accru` finds 8. **A single punctuation
variant caused a 25% false-negative rate on the section that matters most.**

This is fatal to "recurring phrase" as the mechanical definition of a theme, and
it is the reason the design below does not use one.

---

## 1. What is a theme, mechanically?

### What the corpus actually offers

| Property | Measured |
|---|---|
| Reports in the sweep corpus | 15 (12 distinct projects; Chainlink, Aave and Plasma appear twice) |
| Section schema | 24 keys, stable — `3_tokenomics`, `16_bull_case`, `18_key_risks`, `20_mandate_compliance`, … (Plasma `fb190612` has 25) |
| `18_key_risks` shape | JSON array in 14 reports, a bare string in 1 — **nearly uniform, not uniform** |
| Total key-risk items | 70 across the corpus |
| Risk items naming the agent that raised them | **19 of 70 (27%)** |
| Report section prose | 355,249 chars ≈ 88.8k tokens |
| All agent outputs for those evaluations | 218 rows, 1,599,664 chars ≈ 400k tokens |

Two consequences fall straight out.

**The report writer's own attribution cannot be trusted as provenance.** Only
27% of risk items carry a "Raised by …" clause. Any per-agent analysis must read
`agent_outputs` directly rather than parsing the report's prose. This is
fortunate — `agent_outputs` is the more primary source anyway.

**`18_key_risks` is the right unit, but its shape must be defended.** It is the
committee's own compressed statement of what is wrong with a project: 70 items
across 15 reports, roughly five per report, already deduplicated by the report
writer. It is a far better substrate than 24 sections of prose. But one report
in fifteen stores it as a string, so a build must normalise
`jsonb_typeof(...) = 'array'` and fall back, not assume.

### The four candidate approaches

**Recurring phrase / lexicon — rejected as the primary.** Two independent
failures. It is lexically fragile (the hyphen, above). More fundamentally, a
lexicon can only find themes somebody already named. Jacob named this one; the
value of the feature is the *next* one, which nobody has named yet. A lexicon
cannot discover an unnamed theme by construction.

**Embedding neighbourhood — rejected for now, reconsider at ~100 reports.**
The infrastructure exists (`pgvector==0.3.6`, `openai==1.58.1` for
`text-embedding-3-small`, `tools/semantic.py`), so this is cheap to reach for.
It should still be declined at this corpus size. Clustering 70 risk items
requires choosing a cluster count that nothing at n=70 can calibrate, and the
resulting clusters are unlabelled vectors — they still need a naming step before
anything is reportable. Embeddings would add a tuning parameter and a failure
mode without removing either of the steps that actually produce the output.

**Clustered risk statement — the right unit, wrong mechanism.** Correct that
`18_key_risks` is the substrate; wrong that clustering is how to group it.

**LLM label over extracted claims — recommended.** One model call over the 70
extracted risk items (not over 24 sections of prose), asked to assign open
vocabulary theme labels. This is the only candidate with open-vocabulary
discovery, it is immune to the hyphen problem, and it operates on ~3.5k tokens
rather than 400k.

### Recommended shape

Three stages, and the ordering is the point.

**Stage A — structural extraction. No model, no network, free.**
Pull `18_key_risks` (normalised for the string case) from each corpus row via
`_CORPUS_SQL`. Yields ~70 items with project, category and evaluation id.

**Stage B — labelling. One LLM call.**
Send the ~70 items, ask for a theme label per item from an open vocabulary,
plus a short rationale. Output is item → label. Measured input: **3.5k tokens**
for the risk items alone, 6.7k with opportunities included — against 88.8k for
the full report prose and 400k for all agent outputs.

**Stage C — the discriminator. No model, free.**
Pure arithmetic over `agent_outputs`. This is §2, and it is the part that
decides whether any label is worth printing.

The expensive stage is only *discovery*. Every stage that decides anything is
free, which matters for the argument in §4.

---

## 2. Real sector pattern, or artefact of the personas?

This is the crux. With the output settled as human-facing (§5), it is also the
*only* thing standing between a genuine observation and an expensive restatement
of the system's own prompts — there is no downstream consumer to catch a bad
call.

### The artefact is real, and it is measurable

Six instructions across five files tell the committee to discuss value accrual
on every project, unconditionally:

| Location | Text |
|---|---|
| `backend/app/agents/tokenomics.py:11,13` | role: *"…value accrual, utility, and token holder incentive alignment"*; *"…poor value accrual mechanisms"* |
| `backend/app/agents/tokenomics.py:46` | output template heading **`4. VALUE ACCRUAL`** — mandatory, every run |
| `backend/app/agents/report_writer.py:269` | section-3 template: *"Value accrual: the exact mechanism, whether it is live…"* |
| `backend/app/memory/committee/economics/SOUL.md:10` | primary lens: *"Incentive alignment, value accrual, reflexivity, leakage…"* |
| `backend/app/memory/committee/economics/INTERFACES.md:44` | interface heading **`4. Value accrual and leakage`** |
| `backend/app/memory/__init__.py:119` | the mandate itself: *"Revenue-generating protocols with moats, clear value accrual…"* |

And the corpus shows exactly what those instructions produce:

**`tokenomics_analyst` raises value accrual in 15 of 15 evaluations. 100%.**

`report_writer` follows at 14 of 15 (93%), which its own section-3 template
compels. The 13-of-15 rate of value accrual appearing in `3_tokenomics` is
**fully explained by the template** and carries no information about any
protocol whatsoever.

If the feature had shipped counting mentions, its first output would have been
"value accrual is a concern in 11 of 12 projects" — a true sentence, a useless
one, and a restatement of `report_writer.py:269` dressed as a market insight.

### The discriminator

**Exclude the agents whose prompts compel the topic; count the rest.**

`tokenomics_analyst` and `report_writer` are structurally compelled here and are
excluded. The remaining ~13 agents may raise value accrual or not. The rate at
which they do is the signal.

Per-agent, across the 15 corpus evaluations (hyphen-tolerant match on raw
`agent_outputs`):

| Agent | Raises it | Rate | |
|---|---|---|---|
| `tokenomics_analyst` | 15/15 | **100%** | compelled — excluded |
| `report_writer` | 14/15 | **93%** | compelled — excluded |
| `ray_dalio` | 10/14 | 71% | discretionary |
| `devils_advocate` | 10/15 | 67% | discretionary |
| `maturation_scorer` | 10/15 | 67% | discretionary |
| `committee_chair` | 9/15 | 60% | discretionary |
| `competitive_intel` | 9/15 | 60% | discretionary |
| `portfolio_manager` | 8/15 | 53% | discretionary |
| `governance_analyst` | 7/15 | 47% | discretionary |
| `onchain_analyst` | 6/15 | 40% | discretionary |
| `risk_officer` | 6/15 | 40% | discretionary |
| `tech_infra_analyst` | 5/15 | 33% | discretionary |
| `field_intel` | 3/15 | 20% | discretionary |
| `legal_regulatory` | 2/15 | 13% | discretionary |
| `technical_analyst` | 0/8 | 0% | discretionary |

The 100% row is the artefact. Everything below it varies by project, and
variation is what carries information.

### Applied per project, it separates cleanly — and it ranks Jacob's example first

Discretionary agents raising value accrual, per evaluation:

| Project | Category | Eval | Discretionary raising | Decision |
|---|---|---|---|---|
| **Arbitrum** | Smart Contract Platform | `40eaf3d8` | **10 / 13** | PASS (47.2) |
| GMX | DeFi | `8e4b3c83` | 10 / 13 | CHAIR_FAILED (43.4) |
| Chainlink | Infrastructure | `5b566fc1` | 10 / 13 | — |
| **Plasma** | L1 | `fb190612` | **8 / 13** | PASS (31.6) |
| Kamino | DeFi | `e1b7ac31` | 8 / 13 | CHAIR_FAILED (54.7) |
| LayerZero | L1 | `8bcb083b` | 7 / 12 | — |
| Chainlink | Infrastructure | `75cf1b3d` | 6 / 12 | — |
| Hyperliquid | L1 | `be8210d4` | 5 / 13 | WATCH (58.5) |
| Dolphin | AI | `3c5483d5` | 4 / 13 | VETO (27.9) |
| Lombard | L1 | `07035d61` | 4 / 12 | — |
| Aave | DeFi | `1a94e47d` | 3 / 13 | — |
| Aave | DeFi | `c1479a94` | 2 / 12 | — |
| Quai | L1 | `5a57a961` | 2 / 12 | — |
| Plasma | L1 | `d5571fd9` | 1 / 12 | — |
| Polkadot | L1 | `b70d9d7f` | 0 / 12 | — |

Three things to note.

**The naive test cannot distinguish any of these rows** — 14 of 15 mention the
phrase. The discriminator spreads them from 0 to 10 and separates the top five
(8–10) from the bottom five (0–3) with a clear gap.

**Jacob's two examples land at ranks 1 and 4.** His observation survives the
test that kills the artefact. That is the strongest evidence in this document
that the feature is worth building.

**The same project moves.** Plasma scores 1/12 in April and 8/13 in August.
A persona artefact would be constant across projects and across time; this is
not constant, which is what an artefact cannot do.

### Cross-checking the discriminator against other themes

Run over eight candidate themes, comparing the naive count to the count
surviving the discriminator (≥5 discretionary agents):

| Theme | Naive projects | Strong projects | Strong categories | Prompt mentions in repo |
|---|---|---|---|---|
| value accrual | 11 | **7** | **4** | 8 across 5 files |
| emission / dilution | 10 | 6 | 4 | 15 across 11 files |
| unlock overhang | 11 | 5 | 4 | 30 across 14 files |
| governance centralization | 9 | 4 | 4 | 15 across 9 files |
| unproven traction | 8 | 4 | **1** | 4 across 4 files |
| insider concentration | 9 | 3 | **1** | 18 across 11 files |
| revenue not to holders | 5 | 1 | 1 | — |
| fee switch off | 5 | 1 | 1 | 2 across 2 files |

The discriminator does real work — it cuts every theme, and it cuts them
unevenly. `insider_concentration` drops from 9 naive projects to 3, and collapses
to a single category. Value accrual survives at the top on every measure.

### The honest limit

The discriminator establishes that a theme is **not merely an artefact of the
two compelled agents**. It does not establish that the theme is a fact about the
market rather than a fact about *this committee's shared priors* — every agent
reads the same mandate (`memory/__init__.py:119`), which names value accrual.
Ruling that out needs a corpus this system does not have and probably cannot
generate: the same projects evaluated by a committee with a different mandate.

**This should be stated in the output every time**, not buried here. The
observations report is evidence about what the committee concludes. It is not
evidence about what is true of crypto, and the wording must not let a reader
slide from one to the other.

### This doubles as an audit of the committee

The 100% row is a finding in its own right, independent of any theme. A
persona that raises the same concern on 15 of 15 projects — including the ones
where it is not true — is not discriminating between projects on that axis, and
its contribution to the composite score on that axis is close to noise.

The per-agent table should ship as a standing section of the report. Its natural
consumer is `agent/personas`, which owns `backend/app/memory/**`. This is
arguably more valuable than the thematic finding that motivated it, and it is
free.

---

## 3. What makes a theme worth reporting?

Four criteria. **Three calibrate at n=15. One does not, and is not used as a
gate.**

### Calibrated

**C1 — Distinct projects ≥ 3.** Not reports. Chainlink, Aave and Plasma each
appear twice; counting reports would let one project vote twice. Calibrated
against the spread in the table above: value accrual reaches 7 distinct
projects, the next themes 6 and 5, and the tail sits at 1. A floor of 3 sits
below the natural break and above coincidence.

**C2 — Distinct categories ≥ 2.** Jacob's instinct is right that an L2 and a
stablecoin chain sharing a weakness is more interesting than two L2s, and the
measurement supports it: this criterion is what separates `unproven_traction`
and `insider_concentration` (1 category each — both are DeFi-clustered and
unremarkable) from value accrual and unlocks (4 categories each). It does more
filtering work than the project count does.

**C3 — Discretionary corroboration ≥ 5 of ~13 agents, mean across the
projects that carry the theme.** Calibrated against the observed gap: the top
five reports sit at 8–10, the bottom five at 0–3. Five is inside the gap. This
is the criterion that kills persona artefacts and it is the one to raise first
if the report gets noisy.

### Not calibrated — recorded, not gated

**C4 — Correlation with the decision.** Cannot be calibrated at this corpus
size, and proposing a threshold for it would be inventing one.

**Only 6 of the 15 corpus reports carry a recommendation or score at all.** The
nine older evaluations were reconstructed from `agent_outputs` and have no
`reports` row. Those 6 split across four outcome classes: PASS 2,
CHAIR_FAILED 2, VETO 1, WATCH 1. There is no threshold a person could honestly
calibrate on six points and four classes, and any correlation computed on them
would be noise with a decimal point.

**Recommendation: compute and display it, gate nothing on it.** Print the
decisions of the projects carrying each theme, as the table in §2 does, and let
a reader see that both PASS decisions carry high value-accrual support. Revisit
as a gate when the corpus has ~30 reports with `reports` rows. Note that this
becomes measurable on its own as evaluations accumulate — it needs no new
machinery, only time.

**Actionability — a human judgement, not a threshold.** There is no ground
truth for it anywhere in the corpus: nothing records whether a flagged theme
ever changed a decision. It belongs in the report as a prompt for the reader,
not as a filter that silently drops things.

### Frequency is deliberately not a criterion

It is the obvious one and it is the wrong one — 14 of 15 is the highest possible
frequency and means nothing. C1–C3 replace it.

---

## 4. Where does it run, and what does it cost?

### Cost

Rates from `backend/app/llm/pricing.py` (list prices, read 2026-08-27):
Haiku 4.5 `$1.00 / $5.00` per MTok; Sonnet 4.6 `$3.00 / $15.00`.

The Stage B substrate was measured, not guessed: `18_key_risks` across all 15
reports is **13,916 chars ≈ 3.5k tokens**; adding `19_key_opportunities` brings
it to **26,712 chars ≈ 6.7k tokens**. That is the entire model input.

| Stage | Work | Cost |
|---|---|---|
| A — structural extraction | SQL over `_CORPUS_SQL`, ~70 risk items | **$0.00** |
| B — labelling (Haiku, 3.5–6.7k in, ~6k out) | one call over extracted items | **~$0.04** |
| C — the discriminator | arithmetic over `agent_outputs` | **$0.00** |
| **Total per run** | | **~$0.04** |

Output dominates input by an order of magnitude, so the cost is driven by how
verbose the labelling response is, not by corpus size — which is the main reason
Stage A is worth having.

If Stage B is instead run over full report sections (88.8k tokens) rather than
extracted items: ~$0.12 on Haiku, ~$0.36 on Sonnet. If it were run over all
agent outputs (400k tokens): ~$0.43 Haiku, ~$1.29 Sonnet — **this is the
configuration to avoid**, and Stage A exists precisely so it is unnecessary.

**Per month: ~$0.04**, at the monthly cadence recommended in §6. Against a
measured full evaluation cost of ~$3.91 (Plasma `fb190612`, 2026-08-29), one
thematic scan is about **1% of one evaluation**. Cost is not a reason to decline
this feature, and it is not a reason to run it more often than monthly either.

It scales gently: the risk substrate grows ~930 chars per report, so a 100-report
corpus puts ~23k tokens into Stage B — about **$0.12/run**, still dominated by
output length.

**It is not free, unlike the numeric sweep.** The sweep is regex over strings
already in memory. Stage B is a real API call and will fail when the Anthropic
budget is exhausted — which has happened on this project. **Stages A and C must
therefore be able to run and report without Stage B**, degrading to "here is the
per-agent contribution table and the base rates, with no new labels this month".
That degraded output is still the committee audit, which is the half that does
not need a model. A design where an exhausted budget produces nothing repeats
the empty-`consistency_findings` failure.

**Embeddings, if ever added,** would need a price row in `pricing.py` —
`text-embedding-3-small` is a dependency but is not in `USD_PER_MTOK`, and that
file's own comment forbids adding a rate nobody has read off the pricing page.

---

## 5. Where does the output go? — settled: a human, and nothing else

**This is an invariant, not a preference. It must not be relaxed without
re-deriving the argument below.**

### The rule

The thematic scan's output reaches **a person**. It does not reach any agent, any
prompt, `case_context`, a `known_contradictions`-style injection, or a persona
file. There is no code path from an observation to a model input.

### Why — and why this differs from the numeric sweep

The numeric sweep's warnings *do* reach agents through `case_context`, and that
is correct, because a contradiction is a **fact about the data**. "These two
figures cannot both be true" is something an analyst needs in order to avoid
building on a bad number. Withholding it makes the next report worse.

A thematic observation is a **fact about the committee's own output**, and
feeding it back would bias the next evaluation. Telling every agent "value
accrual is often weak" primes them to find it again. The pattern then confirms
itself: the next report raises value accrual *because it was told the corpus
raises value accrual*, and the theme's frequency climbs for reasons that have
nothing to do with any protocol. **That is a feedback loop that manufactures its
own evidence, and it would quietly corrupt the corpus the scan reads.** The
measurement instrument would be wired into the thing it measures.

This is not a new principle for this project. **CONTRACTS §4.2** already holds
that *"Data agents are independent. The eight parallel agents must not see each
other's output. That diversity is the design."* Injecting a corpus-wide theme
summary into every agent's context is a shared prior across all of them at once
— the same independence loss §4.2 forbids, arriving by a different door and
across runs rather than within one.

And §2's discriminator would be destroyed by it. That method works by comparing
compelled agents against discretionary ones. Once every agent has been told what
the corpus concludes, **every agent is a compelled agent**, there is no
discretionary control group left, and the feature loses the only thing that makes
its output trustworthy. The feedback path does not merely bias the committee — it
disables the check that would have detected the bias.

### The temptation, named in advance

Somebody will eventually think: *these observations would be useful context for
the agents.* It is a natural thought — it is the same reasoning that correctly
justifies the numeric sweep's `case_context` path, applied one step too far. It
is wrong for the reasons above.

**A build must carry this argument as a comment in the module that produces the
output, adjacent to the code that would have to be edited to violate it.** This
project's recurring failure is a guard that exists and does nothing; the mirror
failure is a guard removed because nobody wrote down why it was there. A test
asserting that the thematic module is not imported by anything on the prompt-
assembly path would make the invariant enforceable rather than advisory.

### The surface

**A read-only endpoint, plus a Telegram notification on new observations.**

Justified against how Jacob already consumes this system:

- **It mirrors the numeric sweep exactly.** `GET /api/consistency/findings` and
  `GET /api/consistency/schedule` are the surfaces he already uses for the
  sibling feature. A second, different mechanism for the same kind of question
  is a thing to learn for no benefit.
- **It ships with the deploy.** CONTRACTS §4.7 — deploy is `git pull --ff-only`
  plus `docker compose up -d --build`. An endpoint ships on that path.
- **A generated file committed to the repo is actively wrong here**, and this is
  worth stating because it is otherwise the obvious choice. The scan runs
  in-process on the VPS, and the VPS deploys by `git pull --ff-only`. A process
  writing commits into that checkout would break the next fast-forward. The
  output cannot live in the repo.
- **Notion is available** (`tools/notion.py`) but is a write to an external
  system on a cadence, with its own auth and failure modes, for an artefact one
  person reads. The endpoint is strictly less machinery.
- **Telegram for the notification**, because `telegram_bot.py` already exists and
  the numeric sweep already logs new findings at WARNING specifically as a hook
  for it. A monthly report nobody is told about is a report nobody reads.

Every one of these is read by a person and by nothing else.

---

## 6. Relationship to the existing sweep

**Same driver, same tick, separate policy, separate module.**

**Reused unchanged:**
- `consistency_schedule.py`'s loop, advisory lock, timeout and failure recording.
  It is already the guarded, observable, deploy-shipped heartbeat this needs, and
  a second loop would be a second thing to notice has died.
- `_CORPUS_SQL` — the one correct definition of "one evaluation contributes its
  prose exactly once". A second corpus query is the duplicate-definition failure
  D15's branch existed to remove. It should be **imported, not copied**.
- `pricing.py` for the Stage B cost line.

**New:**
- A `thematic_is_due()` policy function of its own, alongside `audit_is_due()`.
  It must not reuse the audit's, because the two cadences differ: the numeric
  sweep has a ten-report burst arm because a contradiction is urgent, and a
  theme is not. Monthly only — the same 2nd-of-the-month boundary, so both run
  on one tick and `sweep_window_start` is reused rather than re-derived.
- A `thematic_observations` table. It must not share `consistency_findings`:
  that table's rows carry `status`, revisions and a supersede path built for
  correctable factual errors, and an observation is neither correctable nor an
  error.
- One module, one endpoint, one Telegram hook.

**Files a build would touch, and who owns them (CONTRACTS §1):**

| Path | Owner | Note |
|---|---|---|
| `backend/app/knowledge/thematic.py` | *unassigned* — new | needs an owner before work starts |
| `backend/app/api/thematic.py` | *unassigned* — new | |
| `backend/app/knowledge/consistency_schedule.py` | current consistency branch | one added call in `run_tick` |
| `backend/app/knowledge/consistency.py` | current consistency branch | export `_CORPUS_SQL` / `sweep_window_start`; no logic change |
| `backend/app/main.py` | `agent/core` | router registration only |
| `backend/migrations/**` | `agent/persistence` | the new table |
| `backend/tests/**` | `agent/qa` | except `test_calibration.py` |
| `telegram_bot.py` | *unassigned* | the notification hook |

Three owners must be routed through the orchestrator: `agent/core`,
`agent/persistence` and `agent/qa`. Nothing in `backend/app/agents/**` or
`backend/app/memory/**` is touched, and that is a consequence of §5 rather than a
coincidence — **a build that finds itself needing to edit a persona file has
violated the invariant.**

---

## What was deliberately left

- **Embeddings and clustering.** Infrastructure exists; declined at n=15 for
  the reasons in §1. Revisit at ~100 reports.
- **A decision-correlation gate.** Not calibratable on 6 scored reports (§3).
  Computed and displayed; gates nothing.
- **Any actionability score.** No ground truth exists in the corpus.
- **Themes at section granularity other than `18_key_risks`.** `3_tokenomics`
  was measured and is template-driven — 13 of 15 by construction. `16_bull_case`
  and `17_bear_case` are candidates worth testing in a build, and were not
  measured here.
- **Separating committee priors from market truth.** Stated as an unresolved
  limit in §2 rather than papered over. It needs a corpus this system cannot
  currently produce.
- **All code.** Nothing in this specification was built. The measurement
  queries used to produce it were throwaway SQL run read-only against
  production and are not part of the tree.
