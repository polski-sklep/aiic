# ADR 0002 — Score / Chair coherence: what the committee's number is for

- **Status:** **Proposed — awaiting Jacob's decision.** Nothing in the scoring
  path has been changed.
- **Date:** 2026-08-24
- **Branch:** `agent/architecture` (analysis only; `agents/orchestrator.py` is
  `agent/persistence`'s file and `agents/chair.py` is unowned)
- **Decides:** `PROJECT_DECISIONS.md` D6
- **Sources:** handoff §3, §3.1, §6.5; `CONTRACTS.md` §2.6, §4.1;
  `agents/orchestrator.py`, `agents/chair.py`, `agents/report_writer.py`

---

## Context

Aave, 11 June 2026: the committee computed **77.20** — above the 75 INVEST
threshold — the Chair returned **PASS** at **high** confidence, and the ledger
records both, side by side, with nothing indicating that anyone noticed
(`CONTRACTS.md` §2.6, row `df935a82…`).

The handoff calls this a defect (§3.1) and predicts it will recur. It is worse
than a defect in the ordinary sense, because there is no code path in which the
two values are compared. Reading the pipeline shows why.

### What actually happens

```
agents/orchestrator.py

222   chair   = await self._run_agent(self.chair, chair_context, on_status)
225   exclude_from_scores = {"report_writer", "ray_dalio", "committee_chair",
                             "technical_analyst"}
231   overall  = self._calc_score(agent_results)
232   decision = chair.output.get("decision", "VETO" if vetoed else "INSUFFICIENT_DATA")
...
261   await record_calibration(
267       recommendation=decision,          # the Chair's string
268       overall_score=overall,            # the weighted arithmetic
269       chair_confidence=str(chair.output.get("conviction_level", "unknown") ...),
```

Five things follow, each verifiable by reading the file:

1. **`overall` is computed at line 231 — after the Chair has already run at
   line 222.** At the moment the Chair is prompted, the number it is supposedly
   contradicting does not exist yet. It cannot reconcile what it was never
   shown.

2. **The Chair is very probably never shown any score at all.** Its prompt
   receives `draft_report` truncated to 6000 characters
   (`agents/chair.py:34`). The Report Writer's score lives in
   `sections["22_overall_score"]` (`agents/report_writer.py:97`) — section 22 of
   24, serialized last. A 24-section narrative report exceeds 6000 characters
   long before section 22. *(Not directly observable today: the `reports` table
   is empty and the 18 June cohort has no `agent_outputs` — `CONTRACTS.md` §2.3,
   §2.5. It is measurable on the next live run by logging `len(json.dumps(report))`.)*

3. **There are three different "scores" in the system and they are not the same
   number.** `_calc_score` (deterministic, weighted, `orchestrator.py:311-331`);
   `sections["22_overall_score"]`, which is an LLM *asked to produce a weighted
   average* and given no weights; and `chair.output["score"]`
   (`agents/chair.py:104`), which the Chair invents. Only the first is written
   to `overall_score`.

4. **The Chair's own score is parsed and then discarded.**
   `base.extract_score` (`agents/base.py:286-291`) reads it into
   `chair.score`, and `committee_chair` is in `exclude_from_scores`, so it
   enters neither `scores` nor `_calc_score`. It survives only inside the
   `agent_results` JSON blob, read by nothing.

5. **The 75/60 thresholds are not in the decision path.** They appear once, in
   `_simple_rec` (`orchestrator.py:333-337`), which runs *only* on the fallback
   branch where the Report Writer failed to emit a `sections` key
   (`orchestrator.py:194-204`), and which uses an **unweighted** mean over a
   different agent set than `_calc_score`'s weighted one. The Chair's prompt
   contains no threshold anywhere.

So 77.2/PASS is not a malfunction. It is two independent estimates, produced by
different methods from overlapping evidence, written to adjacent columns and
never compared. The system has no opinion about whether they agree, because
nothing in it can form one.

### The second, related problem

`WATCH` is free (handoff §6.5). Four of the six usable records are WATCH at
53–66; there are zero BUYs and zero VETOs. A `WATCH` commits to nothing, expires
never, and cannot be scored wrong. The discrimination metric the calibration
ledger exists to compute — did BUYs outperform PASSes — is not weak at n=6, it
is **uncomputable**, and it stays uncomputable at n=60 if the committee keeps
choosing the option that cannot be graded.

These are separable decisions and this ADR treats them separately. Fixing the
score/Chair contradiction does not make the committee convict; making it convict
does not make the number and the judgment agree. **D6 currently runs them
together; they should be decided apart.**

### Constraints any option must respect

- **`CONTRACTS.md` §4.1 — Technical Analyst never influences conviction.** It is
  in `exclude_from_scores` and reaches the Chair only as
  `technical_entry_context` (`orchestrator.py:221`). None of the options below
  touch this.
- **`CONTRACTS.md` §3.1 — `record_calibration`'s signature is frozen.** Options
  needing new fields need `agent/persistence` to add columns and a migration
  (§3.3), through the orchestrator.
- **Eight existing ledger rows are the entire history.** Whatever is chosen, the
  cost of orphaning them is high precisely because there are so few.

---

## Options

### Option A — Keep the cardinal score; make the Chair reconcile the band, on the record

Move `overall = self._calc_score(agent_results)` above the Chair call and put it
into `chair_context` together with the band boundaries. Add a required
`score_reconciliation` block to the Chair's output schema — which band the score
falls in, whether the decision agrees, and if not, the specific evidence that
justifies departing from it. Persist the contradiction flag next to the
recommendation.

The number stays advisory. The Chair keeps final authority. What changes is that
a disagreement becomes a thing the system knows about and stores, rather than an
artefact a human notices two months later while reading a ledger.

**Consequences**

- *Comparability with the 8 existing records:* **fully preserved.**
  `overall_score` keeps its exact meaning and computation, so the seven non-null
  values stay on the same scale as everything that follows. This is the only
  option with zero discontinuity.
- Cheap: reordering two statements, extending one prompt, adding a
  `score_recommendation_conflict boolean` (and ideally
  `score_reconciliation text`) to `calibration_records` via `agent/persistence`.
  No decision semantics change.
- Immediately answers a question nobody can answer now: *how often* does this
  happen? One observed case is an anecdote. If it turns out to be one run in
  twenty, the design is defensible and needs only visibility. If it is one in
  three, the cardinal score is not measuring conviction and Option B follows on
  evidence rather than on a single Aave row.
- **Does not fix the conviction problem.** A committee that reconciles its way to
  WATCH every time is still ungradeable.
- Risk: an LLM asked to justify a departure will always produce a justification.
  The reconciliation text is evidence that the contradiction was *considered*,
  not that it was resolved well. Mitigate by requiring it to name a specific
  agent finding, not a general argument.
- Risk: showing the Chair the number may anchor it, and some of the Chair's
  independence is the point. Real, but currently the Chair's independence is
  accidental (it cannot see the number because of a truncation bug), not
  designed.

### Option B — Replace the cardinal score with ordinal conviction tiers

Retire `overall_score` as the headline. `_calc_score` produces a tier —
say `STRONG / MODERATE / WEAK / INSUFFICIENT` — from the same weighted inputs,
and the Chair returns a decision plus a conviction tier from the same vocabulary.
Agreement or disagreement is then a comparison of two labels drawn from one
scale, which is a check a machine can make.

**Consequences**

- *Comparability with the 8 existing records:* **preserved only if the cardinal
  score keeps being computed and stored as a non-binding diagnostic.** If
  `overall_score` starts arriving NULL, the seven existing numbers become a dead
  series and a ledger with eight rows has just lost most of its continuity — for
  a metric (discrimination) that needs dozens of resolved cases per bucket.
  **If B is chosen, keep writing `overall_score`.** Bucketing old rows into new
  tiers after the fact is possible (77.2 → STRONG, 34.3 → WEAK) but it is a
  reinterpretation, and it must be marked as one in `outcome_notes`, or it
  becomes exactly the class of undocumented reconstruction this project keeps
  paying for.
- Honest about precision. `round(weighted_sum/total_weight, 1)` over ten LLM
  scores that are themselves round numbers out of 100 is false precision; 62.6
  and 65.6 are not distinguishable judgments, and the ledger's `numeric(5,2)`
  rendering (`62.60`) makes it look worse than it is.
- Removes the renormalisation artefact: `_calc_score` divides by the weight of
  agents that *returned* a score, so a run where `tech_infra_analyst` (weight
  0.15, and note it currently runs with **no persona** — `CONTRACTS.md` §2.1)
  fails silently produces a number on a different denominator that reads
  identically to a complete one.
- Larger change, and it is a product change, not a defect fix. It touches
  `orchestrator.py`, `chair.py`, `report_writer.py`, `api/reports.py`,
  `tpl.html`, the Notion writeback, and the ledger — four branches.
- Does not by itself fix conviction either; it makes tier disagreement
  *detectable*, which is the precondition.

### Option C — Pairwise comparison against reference cases

Drop absolute scoring. Score a candidate by asking the committee to rank it
against a library of previously evaluated projects with known outcomes: *is this
stronger or weaker than Morpho was in June 2026, and why?* Conviction is a
position in an ordering, not a number.

**Consequences**

- *Comparability with the 8 existing records:* **effectively nil, and worse — it
  cannot be built yet.** A reference library needs the reasoning behind each past
  case, and per `CONTRACTS.md` §2.5 the 18 June cohort has **no local reasoning
  at all**: no `evaluations`, no `agent_outputs`, no report. It survives only as
  ~9 KB of prose per project in six Notion pages. Reconstructing six usable
  reference cases from Notion is the `agent/retrospective` work item (handoff
  §6.2) and is not finished.
- Theoretically the best fit for the actual problem. Humans and committees are
  far better at "worse than X, better than Y" than at "68 out of 100", and it
  degrades gracefully at small n — which is this system's permanent condition.
- Needs n reference cases with resolved outcomes to be meaningful. There are
  currently zero resolved outcomes: every `price_*`, `return_*` and `alpha_*`
  column is NULL and no checkpoint has ever run (§6.1). Until the backfill (D5)
  and the 16 September 90d checkpoint land, C has nothing to compare against.
- Also the most expensive: it changes the prompt architecture of every synthesis
  agent, and each evaluation must carry the reference set into context.
- **Not currently implementable.** Revisit once the retrospective has produced
  written reference cases and at least one checkpoint horizon has resolved.

### Option D — Change nothing (baseline)

**Consequences:** the ledger keeps accumulating rows where the number and the
decision may silently disagree, and there is no way to tell which. Every future
calibration analysis inherits the ambiguity, and it cannot be repaired
retroactively because the Chair's reasoning for departing was never captured.
The cost of this option rises monotonically with the number of evaluations run.
Recorded here for completeness; it is not defensible past the next evaluation.

### Separable: the conviction question

Independent of A/B/C. `WATCH` currently costs nothing and asserts nothing. Two
cheap changes are compatible with any option above:

- **A WATCH must carry an expiry and a falsifier.** The Chair already produces
  both — `signposts` (`agents/chair.py:99`) and `review_date`
  (`agents/chair.py:102`) — and neither reaches the ledger: `calibration_records`
  has no column for either (`init.sql:128-160`), `signposts` survives only in the
  evaluation result blob (`orchestrator.py:252`), and `review_date` is dropped
  entirely. Requiring a dated review and a named observable that would flip the
  call, and *storing* them, turns WATCH from a non-decision into a testable
  short-horizon prediction — gradeable at n=6. Note this is mostly a persistence
  gap, not a prompting gap: the committee is already producing the material.
- **Record the distribution.** If four of the next six are again WATCH, that is
  itself the finding, and it should be visible on the scorecard rather than
  discovered by hand.

Neither requires deciding A/B/C first.

---

## Recommendation

**Adopt Option A now, keep Option B open, defer Option C, and take the WATCH
expiry/falsifier rule regardless.**

Option A is the only one that is unambiguously a defect fix rather than a
product change: computing a number, letting a judgment contradict it by a full
band, and recording neither the contradiction nor a reason is broken under every
candidate design, including the ones that keep cardinal scoring. It costs a
statement reorder, a prompt field and one boolean column, it preserves all eight
existing ledger rows on their original scale, and it leaves
`CONTRACTS.md` §4.1 untouched.

It is also the only option that generates the evidence needed to choose between
B and C. Right now the case for replacing cardinal scoring rests on a single
row. After A, the base rate of score/decision disagreement is a column you can
query. **Revisit B once roughly twenty further evaluations have run, or sooner if
the conflict rate exceeds ~20%.** If B is then chosen, keep computing and storing
`overall_score` as a diagnostic so the ledger stays continuous. C stays parked
until the retrospective has produced written reference cases and at least one
checkpoint horizon has resolved.

The one thing not to do is Option D.

---

## Status of implementation

**None.** No scoring code has been changed by this branch, per
`PROJECT_DECISIONS.md` D6. `orchestrator.py` is `agent/persistence`'s file;
`agents/chair.py` has no owner in `CONTRACTS.md` §1; new ledger columns require a
migration from `agent/persistence` under `CONTRACTS.md` §3.3. This document
exists to be decided from.
