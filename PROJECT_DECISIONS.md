# Decisions log

Significant judgement calls made while executing the handover in
`AIIC_HANDOFF.md`. Trivial coding choices are not recorded here.

---

## D1 — The handoff brief is treated as evidence, not as truth

**Ambiguity:** the brief states several runtime facts that its own methodology
section (§14.3) warns may have drifted.

**Decision:** every load-bearing claim was re-checked against the live VPS
before any work was planned. Where the runtime disagreed with the brief, the
runtime won and the correction was written into `docs/CONTRACTS.md` §2.

**Corrections found:**

- §5 claims `knowledge_chunks` is 0 rows / 0 embeddings. It is **62 rows, 62
  embeddings**. The Notion→pgvector sync has run. pgvector is populated; what is
  actually true is that no *agent* queries it.
- §6.2 assumes the six committee reports are readable for the retrospective.
  The `reports` table is **empty**, and the 18 June cohort has no `evaluations`
  or `agent_outputs` rows either — it was run by the concordance harness, which
  bypassed the API. The reasoning survives only in Notion.
- §2.1 is not actually unresolved: `agent_personas.py::AGENT_FOLDERS` is a
  complete explicit map. Reading it exposed a different, worse defect —
  `tech_infra_analyst` is missing from it entirely.

**Consequence:** the retrospective sources from Notion, not Postgres. pgvector
activation drops off the work list; the real gap is retrieval wiring, not sync.

---

## D2 — Uncommitted working-tree changes were committed, not stashed

**Ambiguity:** two files were modified and uncommitted at handover.

**Decision:** committed both to `main` as `5d3c033` before branching.

**Reasoning:** the `.gitignore` `.obsidian/` line is intentional and documented
in §12. The `CAST(:embedding AS vector)` fix in `knowledge/__init__.py` is
correct — `::vector` collides with SQLAlchemy's `:param` binding — and the brief
said to commit it "when pgvector work begins". Since pgvector turns out to hold
62 live embeddings (D1), that code path is no longer dormant.

**Consequence:** no user work can be lost to a checkout or worktree operation.

---

## D3 — Tree 4 is triaged by reapplication, never by copy

**Ambiguity:** `~/Projects/committee-orchestrator` holds eight uncommitted
cleanup passes on a base that predates `technical_analyst.py`,
`api/calibration.py`, `knowledge/calibration.py` and `tools/binance.py`.

**Decision:** port individual passes onto `integration` by reapplying the
intent, one at a time, each verified separately. No file is copied across.
Tree 4 is left untouched — not reset, not deleted, not merged.

**Reasoning:** §14.6 of the brief; a naive copy would silently roll back four
load-bearing modules that exist only on the newer base.

**Consequence:** only pass #4 (the circular-dependency fix via
`tools/contracts.py`) is being ported now. The other seven are assessed in
`docs/triage-tree4.md` with a recommendation each.

---

## D4 — Risk Officer thresholds adopt the brief's own proposals as defaults

**Ambiguity:** §11 lists four unresolved thresholds on the closed veto list and
says they need Jacob's input.

**Decision:** adopt the proposal recorded beside each one in the brief, rather
than block:

| # | Condition | Adopted threshold |
|---|---|---|
| 1 | Unaudited contract | Veto only if unaudited **and** it is the contract funds enter; otherwise flag |
| 2 | Upgradeable, single-key admin | No timelock or <24h → veto; ≥24h with a public upgrade queue → severe flag |
| 4 | Mutable mint authority | Veto only on live + uncapped + single-key + no timelock; renounced/timelocked/capped → flag |
| 6 | Prior rug by same team | Veto on **verified** attribution only (on-chain link, or doxxed/admitted); credible allegation → severe flag |

**Reasoning:** Jacob already said "all of them" to the six conditions and the
brief records a specific proposal for each threshold. Each proposal is the
conservative reading — it narrows the veto rather than widening it, consistent
with settled decision 1 ("veto fires on presence of danger, never absence of
evidence"). These are cheap to revise: they are markdown, not code.

**Consequence:** the Risk Officer persona is written now rather than blocked.
Every adopted threshold is marked in the persona files so it is obvious what to
revisit.

---

## D5 — The calibration backfill does both 30d and the 67-day mark

**Ambiguity:** §6.4 puts option (a) true 30d backfill and option (b) one-off
67-day mark-to-market to Jacob, unanswered.

**Decision:** do both, as the brief's own recorded lean suggests. 30d
checkpoints are written with true historical prices and the true observation
date (11 July for Aave, 18 July for the five-project cohort). The 67-day figure
goes into `outcome_notes`, not into a dated column.

**Reasoning:** they answer different questions and the marginal cost is about
seven CoinGecko history calls. Leaving the columns NULL is not the safe option —
it means the 90d checkpoint on 16 September lands on an incomplete series.

**Safeguards:** a full `pg_dump` was taken before any write
(`~/aiic-backups/aiic-db-backup-20260824.sql.gz`); the backfill script defaults
to `--dry-run`; every backfilled row is stamped in `outcome_notes` so a
reconstructed checkpoint can never be mistaken for a timely one.

---

## D6 — The score/chair incoherence is surfaced, not silently resolved

**Ambiguity:** §3.1 (Aave scored 77.2 → INVEST band, chair returned PASS at high
confidence) and §6.5 (no conviction mechanism) both point at the same design
flaw. §6.5 explicitly says the fix is Jacob's decision.

**Decision:** do not change decision semantics unilaterally. Implement the part
that is unambiguously a defect — the disagreement is currently *invisible* — and
put the design choice to Jacob as a written ADR with a recommendation.

**Reasoning:** replacing cardinal scoring with ordinal conviction tiers, or
penalising deferral, changes what the product *is*. That is not an
implementation detail. But a system that computes 77.2, returns PASS, and
records neither the contradiction nor a reason is defective under any of the
candidate designs.

**Consequence:** `docs/adr/0002-score-chair-coherence.md` states the options.
Nothing in the scoring path changes until Jacob picks one.

---

## D7 — No frontend application is being built

**Ambiguity:** the README says "Frontend: Next.js 15 (planned)".

**Decision:** no Next.js app. The existing HTML report view
(`api/reports.py` + `tpl.html`) is treated as the UI surface and brought to
production quality.

**Reasoning:** the handover's work list contains no frontend item, and the
system's only human surfaces are the HTML report and the Telegram bot. Building
a dashboard nobody asked for would displace the work that was actually
requested.

**Consequence:** `agent/ui-report` hardens what exists — the `marked` CDN
dependency, the `innerHTML` injection of LLM-authored markdown, print styling,
responsive layout, empty and error states.

---

## D8 — Deployment to the VPS is not performed automatically

**Ambiguity:** the deploy path is `git pull --ff-only` on the VPS and is fully
available to this session.

**Decision:** integrate to `main` locally; do not push to GitHub and do not pull
on the VPS without Jacob saying so.

**Reasoning:** the VPS is a live system with a six-week uptime and the only copy
of the calibration ledger. Publishing to a public repository and mutating a
running production service are both outward-facing, and neither was asked for.

**Consequence:** the handover ends with the exact two commands to deploy.

---

## D9 — The score/chair incoherence is a sequencing defect, not a philosophy problem

**Discovered:** `agent/architecture`, confirmed independently by the orchestrator.

`orchestrator.py` runs the Chair at line 222 and computes
`overall = self._calc_score(...)` at line 231. **The weighted score does not
exist when the Chair decides.** The Chair's own score is parsed and then
discarded (`committee_chair` is in `exclude_from_scores`). The 75/60 thresholds
live only inside `_simple_rec`, which runs on the report-failure branch over an
*unweighted* mean. And `chair.py` truncates the serialised 24-section draft
report to 6000 characters with a raw byte slice, so the score in section 22 is
very likely cut before the Chair ever sees it.

Nothing compares the score to the decision **because nothing can**.

**Decision:** split the fix along the line D6 draws.

- *Defect half, done now:* compute the score before the Chair call, detect
  band-vs-decision contradictions, and record them. Decisions are unchanged
  because the Chair's prompt is unchanged.
- *Semantic half, Jacob's call:* showing the Chair the number and requiring it
  to reconcile. That changes what the committee decides.

**Reasoning:** ADR 0002 recommends measuring the conflict rate before choosing a
redesign. You cannot measure it today. This builds the instrument without
prejudging the design question.

**Evidence this is not academic:** Aave, the one record where score and
adjudicator disagreed by a full band, went from $63.09 (11 Jun) to $95.69
(11 Jul) — about **+52% against BTC's +1.8%**. The score said INVEST, the Chair
said PASS, and the score appears to have been right. n=1, and one bad override
is not evidence that overrides are bad — which is precisely why the conflict
rate needs measuring rather than assuming.

---

## D10 — The documented veto override does not exist

`README.md` and handoff §3 both state "the Chair can override a veto with
documented reasoning". The code does `if vetoed: decision = "VETO"`
unconditionally, and `chair.py`'s prompt tells the Chair "you may acknowledge
the veto but cannot override it".

Code and prompt agree with each other; **the documentation is wrong.** This is
the fifth documented-vs-actual divergence found in this system.

**Decision:** fix the documentation, do not implement the override. Whether the
Chair *should* be able to override a veto is a governance question about where
final authority sits, and it interacts directly with the Risk Officer's newly
narrowed veto scope (§11). It goes to Jacob.

---

## D11 — Propagating settled Risk Officer decisions is execution, not governance

Three veto lists reach the Risk Officer at runtime and they conflict: the
rewritten persona's closed list of seven, six hardcoded triggers in
`agents/risk_officer.py`, and six more in `memory/risk_policy.md` marked "No
override possible". `risk_policy.md` vetoes on "Active SEC/DOJ investigation",
which handoff §11 decision 2 explicitly reclassifies as thesis risk rather than
a trap.

**Decision:** align all three to the §11 settled decisions and the D4
thresholds, rather than leaving the contradiction live pending Jacob's review.

**Reasoning:** §11 records those four decisions under "Decisions Jacob has made
(settled)". `risk_policy.md` and `risk_officer.py` predate them and were never
updated. Aligning them implements his decision; it does not make a new one.
Three contradictory lists reaching one agent is worse than any single list.

**Safeguard:** every substantive change is reported as an explicit before/after
line naming the decision that drives it, for Jacob's sign-off. Anything that is
genuinely a new policy question — the old `>$10M TVL` size test being the likely
candidate — is flagged rather than decided.

---

## D12 — Handoff §3.1 is wrong about its own headline case

**Established by:** `agent/retrospective`, from the Aave `adjudication_trace`
recovered out of `agent_outputs` — the one project in the corpus with Postgres
rows as well as a Notion page.

§3.1 says Aave "scored 77.2 with chair confidence high, and the chair returned
PASS — the weighted score and the chair's judgment disagreed by a full band and
the score lost."

**The Chair never received 77.2.** It read the Report Writer's **73.5** — a
WATCH-band number — and recorded a full trace: `threshold_crossed`,
`override_reasoning`, fatal versus non-fatal objections, and
`report_writer_recommendation: WATCH`. The weighted 77.2 was computed nine lines
later and written to the ledger.

So the two numbers never met. The score did not lose an argument; there was no
argument.

**Why this changes what should be built:** ADR 0002's Option B — replace
cardinal scoring with ordinal conviction tiers — is a remedy for a judgment
agent overriding a number it disliked. That is not this system's defect. The
defects are that the Chair adjudicates on a *different* number from the one the
ledger records, and that its reasoning is discarded: `_notion_write` saves agent
summaries only, and four of six records have no `agent_outputs` at all.

**Consequence:** Option A's premise strengthens and Option B's weakens. D6's
split stands, but `agent/core` must record **both** numbers, not just the
weighted one against the decision. Persisting the Chair's decision object is
promoted from nice-to-have to a prerequisite for calibration meaning anything.

---

## D13 — §6.2's bear-market assumption does not hold

§6.2 warns that "in a bear market every PASS/WATCH looks correct on price".
Measured over the actual window, **BTC rose 21.4%**. It was not a bear market,
so raw return flatters nothing and alpha is doing real work rather than
rescuing a foregone conclusion.

A second, sharper caveat came out of the measurement and is Jacob's to weigh:
**two of six verdicts change sign across the six days from 18 to 24 August**,
when the market repriced broadly. Ethena grades as a clean HIT on 18 August and
a PARTIAL on 24 August on identical reasoning. Only Aave and Plasma are
sign-stable at every checkpoint.

**Consequence:** at n=6 and 67 days, the *direction* of individual verdicts is
not robust to the observation date. The qualitative findings — which classes of
variable the committee notices and which it misses — are robust, because they
rest on what was written rather than on where the price landed. Weight the
findings, not the scorecard.
