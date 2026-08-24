# Recommendations

Ranked by expected value = (size of the error it prevents) × (probability it
recurs) ÷ (cost to implement). Each names the artifact it changes, the owning
branch per `docs/CONTRACTS.md` §1, and the evidence in `02-findings.md`.
Speculative items are marked and ranked last.

---

## R1 — Guard the CoinGecko 429-that-returns-200 before any checkpoint is written

**Artifact:** `backend/app/knowledge/calibration.py::update_checkpoint`
(owner: `agent/calibration`)
**Evidence:** `00-method.md` § "The 429-that-looks-like-200 hazard"
**Cost:** a few lines. **Do this first.**

`docs/CONTRACTS.md` §3.2 requires the rewritten checkpoint to fetch price via
`/coins/{id}/history?date=DD-MM-YYYY`. On the free tier that endpoint answers a
rate-limit with **HTTP 200** and a body of:

```json
{"status":{"error_code":429,"error_message":"You've exceeded the Rate Limit."}}
```

No `market_data` key. Code that treats a missing `market_data` as "no price for
that date" will write a rate limit into the ledger as a fact about the market,
or worse, write a partial checkpoint. Observed firing on the fourth call at 8 s
spacing.

Required: check `status.error_code` before reading `market_data`; treat it as
retryable, never as absence; back off 15–20 s; raise rather than write on
exhaustion. Add `&localization=false` to cut payload.

The 90-day checkpoint for the 18 June cohort falls on **16 September 2026** and
will be seven history calls in quick succession. That is precisely the shape
that triggers this.

## R2 — Persist the Chair's decision object

**Artifact:** `backend/app/agents/orchestrator.py::_notion_write`, plus the
`record_calibration` call site (owner: `agent/persistence`)
**Evidence:** F5, F8, and the §6.5 assessment in `02-findings.md`
**Cost:** small. **Highest value per line in this document.**

Five of six records in this corpus have no Chair reasoning anywhere.
`_notion_write` persists per-agent summaries and a top-five risk list; the
Chair's `decision`, `reasoning`, `signposts`, `review_date`, `confidence`,
`mandate_flags` and `adjudication_trace` are written to `agent_outputs` only —
and the 18 June cohort has no `agent_outputs` rows at all. So for GEODNET,
Ethena, Morpho, Pendle and the graded Plasma run, **the reason for the
recommendation does not exist.**

The Aave record shows exactly what is being thrown away: five concrete falsifiable
signposts, an explicit `threshold_crossed`, `objections_judged_fatal` versus
`non_fatal`, and `report_writer_recommendation: "WATCH"` against the Chair's
PASS. That single object is the difference between a gradeable decision and an
opinion.

Two changes: write the Chair object into the Notion page body alongside the
agent summaries, and — per `docs/CONTRACTS.md` §3.1 — start passing a real
`evaluation_id` into `record_calibration` so the ledger can reach the trace at
all. Today all eight rows have `evaluation_id IS NULL` (§2.4).

## R3 — Require every named risk to carry a date and a direction

**Artifact:** the risk-emission instruction in
`backend/app/memory/committee/**` personas (owner: `agent/personas`), and the
risk shape assembled in `orchestrator` (owner: `agent/persistence`)
**Evidence:** F1, F6
**Cost:** moderate — a schema line per persona plus an extraction change.

The corpus's one clean HIT is the one risk that carried a date and a size:
*"1B XPL, July 28 2026, ~39.8% of circulating supply."* Every other named risk
is an undated condition, and three are stale or trivial — Morpho's top-five
cites an unlock eight months past in the present tense; Ethena's cites April
2025; Pendle's elevates a seven-day TVL wiggle.

Require each risk to declare: `resolves_by` (a date or `structural`),
`direction` (`bearish` / `bullish` / `either`), and `magnitude` (percentage of
float, of revenue, of TVL — or `unquantified`). Reject `unquantified` +
`structural` + no date as a top-five entry; that combination is a description,
not a risk.

This is what makes every later recommendation possible: an undated risk cannot
be watched, cannot be scored and cannot become a signpost.

## R4 — Fix the "top five deduplicated risks" — it is one agent's list

**Artifact:** `backend/app/agents/orchestrator.py::_notion_write`
(owner: `agent/persistence`)
**Evidence:** F5
**Cost:** small.

Twenty-five of the thirty surviving risk lines across six reports carry
`[tokenomics_analyst]`. Five of six reports are 100% one agent. That is not
deduplication; it is head-truncation of an unsorted concatenation. The Risk
Officer's risks, Ray's risks and the Devil's Advocate's ten challenges never
survive.

Minimum fix: select round-robin across agents before truncating, so the five
represent five viewpoints. Better: rank by (weight of emitting agent × number of
independent agents naming the same risk), which would have surfaced Plasma's
28 July unlock (six agents) and Ethena's funding-rate dependence (five agents)
as the top lines — both of which are, on this evidence, the corpus's most
predictive lines.

Because Notion is the only durable record, this defect has already cost the
project once.

## R5 — Give `tech_infra_analyst` a persona

**Artifact:** `backend/app/memory/agent_personas.py::AGENT_FOLDERS` and a new
`backend/app/memory/committee/` folder (owner: `agent/personas`)
**Evidence:** F7, plus `docs/CONTRACTS.md` §2.1
**Cost:** trivial to wire; a day to write good persona content.

`tech_infra_analyst` carries the joint-highest score weight (0.15), is missing
from `AGENT_FOLDERS`, and runs on `role_description` because
`load_agent_persona` returns `""`. It is also the second-most optimistic agent
measured, **+7.3 points above the composite** and above it on five of six
projects. A 15% weight on an unconstrained generic prompt is the most plausible
explanation, and the cheapest thing here to test.

While in that file: `technical-analyst/` holds one file against four to six for
every other agent, and `knowledge-agent/` is an orphan folder mapped to nothing.

## R6 — Make the upside branch mandatory

**Artifact:** `backend/app/memory/committee/economics/`, `fed-intelligence/`,
`legal-analyst/`, `competitive-intel/` personas (owner: `agent/personas`)
**Evidence:** F2, F3, F4
**Cost:** moderate; markdown only, and cheap to revise.

Four of six projects turned on a value-accrual mechanism and the committee
framed all four as *the risk that the mechanism fails to arrive*. There is not
one scenario in six reports where a fee switch, buyback or burn activates and
the price responds. Aavenomics 3.0 shipped sixteen days after the committee
called it politically impossible.

Likewise `field_intel` sits **−17.0 below the composite** with a one-directional
headwind narrative on all six, and `legal_regulatory` produces structure — entity,
jurisdiction, enforcement — and never a forward catalyst. The largest price event
in the window was macro-regulatory and positive.

Require each of these four personas to emit, symmetrically with its risks, a
short list of **dated catalysts with a bullish direction** under the same R3
schema. Not price targets — events: scheduled votes, activation thresholds
already met, filings, listings, integrations.

Explicitly *not* recommended: letting data agents see each other's output.
`docs/CONTRACTS.md` §4.2 makes independence load-bearing and this evidence does
not challenge it. On Aave the information was never lost — the Devil's Advocate,
which sees everything, stated the buyback thesis and rejected it. The failure was
adjudication, not collection.

## R7 — Grade at more than one date; never read alpha off a single day

**Artifact:** `backend/app/knowledge/calibration.py` and the
`calibration_records` columns (owners: `agent/calibration`, `agent/persistence`)
**Evidence:** `00-method.md` contamination 1; the Ethena and Pendle sections of
`01-per-project.md`
**Cost:** small, and mostly already implied by the 30/90/180 schema.

Ethena's alpha is **−13.1 at 30 days, −12.7 at 61 days, +49.4 at 67 days**. The
same reasoning grades as a clean HIT or a PARTIAL depending on a six-day
difference in when you look. Pendle's sign flips twice. Two of six verdicts are
endpoint artefacts.

Two consequences. First, the 30/90/180 series must be kept complete — a single
late mark-to-market is not a substitute, and `docs/CONTRACTS.md` §3.2's rule that
price be fetched *as of the target date* is what makes the series meaningful.
Second, any scorecard built on these columns should also carry a
**BTC-return-over-window** figure so a reader can see immediately whether a
result is beta: at the 30-day marks BTC moved +1.8% and +1.5%, at 67 days
+21.4%. The same alpha number means very different things in those two regimes.

Use `outcome_notes` (§3.3) for the 67-day mark. Do not let it into a dated
column.

## R8 — Let data quality reach the confidence field

**Artifact:** `backend/app/agents/orchestrator.py` (owner: `agent/persistence`),
consumed by `calibration.py`
**Evidence:** F9
**Cost:** moderate.

Plasma was recorded `chair_confidence: high` on inputs whose own
`onchain_analyst` reported TVL estimates spanning "~$551M to $18.7B… with no
verified DeFiLlama figure resolvable at evaluation time". GEODNET's
`technical_analyst` returned no technical analysis at all. Ethena lost two agents
to truncated JSON — `score: None` — and renormalisation silently dropped
`legal_regulatory`'s weight, so Ethena's 53.2 is computed over nine weighted
agents and GEODNET's 62.6 over ten, with nothing in the ledger recording the
difference.

Minimum: count tool failures and null-scoring agents per run, persist the count
and the denominator, and cap `chair_confidence` when either exceeds a threshold.
A high-confidence call on contradictory data and a high-confidence call on clean
data must not be indistinguishable in the ledger — that distinction is the whole
point of a calibration ledger.

## R9 — Put the conviction question to Jacob as a decision, not a defect

**Artifact:** `docs/adr/0002-score-chair-coherence.md` (owner:
`agent/architecture`; D6 in `PROJECT_DECISIONS.md` already commits to this)
**Evidence:** the §6.5 assessment in `02-findings.md`

Two corrections that ADR should carry, because the handoff's framing is wrong in
a way that would misdirect the fix:

1. §3.1 describes the Chair as overriding a computed score with no recorded
   reasoning. It did not. It produced its own score (73.5, not 77.2), recorded
   `report_writer_recommendation: "WATCH"`, `risk_officer_approved_override:
   true`, `threshold_crossed`, `override_reasoning`, and fatal versus non-fatal
   objection lists. The adjudication is sound in form. **The defect is that the
   ledger cannot see it** — `evaluation_id` is NULL and none of it is persisted
   outside `agent_outputs`. Fix R2 before redesigning scoring.
2. The Aave override was wrong on this one occasion and the cost is measurable:
   a hard mandate exclusion on holder concentration produced a PASS on the
   cohort's best performer, +49.9 alpha at 30 days. **n = 1.** One override being
   wrong is not evidence that overrides are wrong, and PASS is not a short — the
   cost is opportunity, not loss. What it does justify is asking whether
   *holder concentration specifically* should be a hard exclusion, given that on
   this evidence concentration was the enabling condition for the value-accrual
   upgrade that drove the move, not a brake on it.

The underlying asymmetry stands and is the real decision: no agent in fifteen can
raise conviction. The Risk Officer has `veto: false` / `veto_reason` / a
six-trigger boolean map and there is no symmetric object anywhere. The
`portfolio_manager`'s most bullish language across six reports is "a small
starter", "a modest, patiently-sized initial position". Whether to add conviction
authority, and whether deferral should cost anything, is Jacob's call and is
prerequisite to calibration meaning anything.

---

## Speculative — lower confidence, listed for completeness

**S1 — A distribution/listings tool.** F3 shows three of six moves triggered by
distribution events (Upbit listing, FalconX facility, Standard Chartered
coverage / Kraken stake) and none of the eleven registered tools
(`docs/CONTRACTS.md` §3.6) covers that class. Speculative because there is no
obvious free, reliable API for exchange listing roadmaps or institutional
partnerships; the realistic version is a targeted `web_search` prompt in the
`competitive-intel` persona rather than a new tool. Try the persona change
first — it costs nothing and would test the hypothesis.

**S2 — Score the Devil's Advocate.** F8: it produced the corpus's most
consequential error (dismissing the Aave buyback sixteen days before it shipped)
at zero cost, sits 27 points below the composite by construction, and carries no
weight. Scoring its counters would require R3 first — an unfalsifiable counter
cannot be graded either. Speculative because a Devil's Advocate that fears being
wrong may stop being adversarial, which would destroy the thing it is for.

**S3 — Re-run the six evaluations with a working CoinGecko key.** F9 shows the
18 June cohort reasoned on contradictory data. A re-run under clean data, scored
against the same 67-day outcomes, would separate "the committee watches the
wrong variables" from "the committee could not see the variables". Speculative
because it costs real money, `concordance_harness.py` is archived outside the
repo by deliberate choice (§10), and it would not be a like-for-like comparison —
the agents would now be reasoning with hindsight-contaminated web search.

---

## What this corpus could not establish

- **Whether the committee has skill.** Zero BUYs; discrimination is uncomputable.
  Six records, four of them the same recommendation from the same batch.
- **Why four of six WATCHes were issued.** The Chair's reasoning was never
  persisted for the 18 June cohort. This is the single largest hole and R2 closes
  it for future runs but cannot recover the past.
- **What drove Morpho.** Ecosystem integrations and a small unlock do not
  plausibly account for +23.5 alpha; most of it is the 19–24 August market.
  Driver not established.
- **Whether Pendle's outcome means anything.** Alpha of +7.6 / −8.2 / +8.1 across
  three defensible dates is noise.
- **Whether any of this generalises.** Every finding above is a pattern across
  six evaluations run over eight days by one pipeline version. Treat them as
  hypotheses with evidence attached, not as measurements.
