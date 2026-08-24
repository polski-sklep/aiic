# AIIC (Committee Orchestrator) — Full Handoff Brief

**Written:** 24 August 2026
**For:** Claude Code, taking over from a long chat session
**Owner:** Jacob (`polski-sklep`)

Read this whole document before touching anything. It contains verified facts, known failures, unresolved decisions, and one active work item. Where something is unverified, it says so. Do not treat prose in this document as a substitute for checking the runtime.

---

## 0. STOP — read this first: there are FOUR trees, and one brief that points at the wrong one

A separate chat produced a document titled *"Committee Orchestrator: Full Migration Brief"*. **That document is stale and points at the wrong repository.** It says:

- active repo `/Users/Jacob/Projects/committee-orchestrator`
- branch `master`, HEAD `e1c2b52 add telegram bot`
- remote `origin` = `root@100.95.239.105:/opt/WorkspAIce/committee-orchestrator`

None of that is the deployed system. `/opt/WorkspAIce/committee-orchestrator` is not the deployment path. `committee-orchestrator` on the Mac is the abandoned Cowork tree.

### The four trees, as of 24 Aug 2026

| # | Location | State | Use |
|---|---|---|---|
| 1 | `github.com/polski-sklep/aiic` (public) | `main` @ `8432cf4` | **Canonical** |
| 2 | `~/Projects/aiic` (Mac) | clone of #1 @ `8432cf4`, 2 files uncommitted | **Editing copy** |
| 3 | `/opt/committee-orchestrator` (VPS) | clone of #1 @ `8432cf4`, `.env` restored | **Running system** |
| 4 | `~/Projects/committee-orchestrator` (Mac) | `master` @ `e1c2b52`, **dirty**, remote points at a VPS path | **ABANDONED — do not deploy from** |

Trees 1, 2, 3 were reconciled during the session that produced this brief. They agree. Tree 4 is the pre-reconciliation Cowork copy.

### The complication: tree 4 contains real, uncommitted work

The stale brief documents eight cleanup passes performed on tree 4 and **never committed**:

1. DRY consolidation through `prompt_utils`
2. Shared type consolidation in `utils/types.py` (`ToolError`, `SerializedAgentResult`, `AgentResultsByName`)
3. Unused-code removal (`reload_personas`, `ToolRegistry.get_func`)
4. **Circular dependency fix** — new file `backend/app/tools/contracts.py`, tool modules import `ToolArguments` from `contracts` not `registry`
5. Weak-type narrowing at JSON boundaries
6. Error-handling narrowing (Telegram HTTP/JSON, tool registry tracebacks)
7. Legacy/fallback cleanup in `llm/`
8. Comment/doc cleanup

Validation claimed for that work: compileall on edited modules, `python3 -m unittest tests.test_prompt_utils` (6 passed), `git diff --check` clean, AST import-graph scan zero cycles. **Not** a full suite, **not** live evaluation.

`contracts.py` does **not** exist in tree 1/2/3. Confidence: high — it was absent from the file listings taken during reconciliation.

### Required first action

**Do not merge tree 4 into tree 2 blind, and do not resume work in tree 4.** Instead:

```bash
# Confirm the divergence before deciding anything
cd ~/Projects/committee-orchestrator && git status --short | head -50
git -C ~/Projects/committee-orchestrator remote -v
diff -rq ~/Projects/committee-orchestrator/backend/app ~/Projects/aiic/backend/app \
  --exclude=__pycache__ --exclude='*.pyc' | head -60
```

Then present Jacob with a triage list: which of the eight cleanup passes are worth porting onto `aiic`, one at a time, verified individually. The circular-dependency fix (`contracts.py`) is the most likely to be worth having. The rest need judgment.

**Rule established this session, applies here:** port changes onto the newer base by reapplying them, never by copying older files over newer architecture.

---

## 1. What the system is

A self-hosted multi-agent AI investment committee that evaluates crypto projects. Fifteen agents run a staged pipeline and produce `BUY` / `PASS` / `WATCH` / `VETO` with a 24-section report.

Stack: FastAPI · Postgres 16 + pgvector · Redis · Anthropic Claude (OpenAI provider present as alternative) · Docker Compose.

### Pipeline shape

```
evaluate request
  → 8 data agents      (parallel, independent — do not see each other)
  → 4 synthesis agents (sequential, adversarial — each sees prior)
  → Report Writer
  → Ray (independent contrarian pass, post-report)
  → Chair (final decision, sees full record incl. disagreement)
```

Independence in the data layer is deliberate: if those agents saw each other's output they would converge and the diversity is lost.

---

## 2. The roster — VERIFIED from `agents/orchestrator.py`

**Data layer** (`self.data_agents`, parallel):

| Class | Persona slug (see §2.1) |
|---|---|
| `TokenomicsAnalyst` | ? |
| `GovernanceAnalyst` | `gov-analyst`? |
| `OnChainAnalyst` | `onchain-analyst` |
| `TechInfraAnalyst` | ? |
| `CompetitiveIntel` | `competitive-intel` |
| `FieldIntel` | `fed-intelligence`? |
| `LegalRegulatory` | `legal-analyst`? |
| `TechnicalAnalyst` | `technical-analyst` |

**Synthesis layer** (sequential): `MaturationScorer`, `DevilsAdvocate`, `RiskOfficer` (veto authority), `PortfolioManager`

**Structural**: `ReportWriter`, `RayDalio`, `CommitteeChair`

### 2.1 UNRESOLVED: persona slugs do not map cleanly to agent classes

The 15 persona directories under `backend/app/memory/committee/` are:

```
competitive-intel(5)  devils-advocate(4)  economics(5)     fed-intelligence(4)
gov-analyst(4)        governance-chief(4) knowledge-agent(6) legal-analyst(5)
onchain-analyst(4)    portfolio-manager(5) ray-judge(4)     report-writer(5)
risk-officer(5)       technical-analyst(1) valuation-scorer(4)
```

(number = count of `.md` files)

Names like `economics`, `valuation-scorer`, `knowledge-agent`, `governance-chief` do **not** obviously correspond to any instantiated agent class. Either the persona set drifted from the roster, or `agent_personas.py`'s filename map handles the translation.

**Action required:** read `backend/app/memory/agent_personas.py` and produce a definitive class→slug mapping. Until that exists, do not assume editing a persona file affects the agent you think it does. Confidence that this needs checking: high.

Note also `technical-analyst` has only **1** file (`SOUL.md`) against 4–6 for every other agent. That is a real content gap from the rebuild, not a bug.

### 2.2 Institutional memory files

`backend/app/memory/` holds four committee-wide files: `mandates.md`, `risk_policy.md`, `thesis.md`, `trusted_accounts.md`. 74 markdown files total across the memory tree.

---

## 3. Scoring — VERIFIED from `orchestrator._calc_score`

```python
weights = {
    "tokenomics_analyst": 0.15,
    "onchain_analyst":    0.12,
    "tech_infra_analyst": 0.15,
    "governance_analyst": 0.08,
    "competitive_intel":  0.10,
    "field_intel":        0.05,
    "risk_officer":       0.15,
    "maturation_scorer":  0.10,
    "legal_regulatory":   0.05,
    "portfolio_manager":  0.05,
}
```

Renormalised over agents that actually returned a score, so a failed agent does not drag the average.

```python
exclude_from_scores = {"report_writer", "ray_dalio", "committee_chair", "technical_analyst"}
```

**Technical Analyst exclusion is a load-bearing design constraint.** Its output reaches the Chair as `technical_entry_context` (orchestrator.py:221) and informs entry timing and signposts, but must never influence conviction. Any scoring work must preserve this.

Thresholds: ≥75 `INVEST` · 60–74 `WATCH` · <60 `PASS`. Risk Officer veto overrides score; Chair can override veto with documented reasoning.

### 3.1 KNOWN DEFECT: score/chair incoherence

In the live calibration data, **Aave scored 77.2 (above the 75 INVEST threshold) with chair confidence `high`, and the chair returned `PASS`.** The weighted score and the chair's judgment disagreed by a full band and the score lost.

This is not a one-off; it is the predictable result of computing a cardinal score to 2 decimals and then letting a judgment agent override it. Recommended direction (not yet implemented): replace cardinal scoring with ordinal conviction tiers or pairwise comparison against known reference cases, and never let a computed number contradict the adjudicator.

---

## 4. Tools — 11 registered, VERIFIED from container startup logs

```
get_price, get_token_info                                    (CoinGecko)
get_tvl, get_protocol_fees                                   (DeFiLlama)
get_klines, get_orderbook_depth, compute_technical_levels    (Binance public)
web_search                                                   (Brave)
search_twitter                                               (X)
search_notes, read_note                                      (Notion)
```

Registered via `tools/registry.py` (log source: `[app.tools.registry]`).

**Not built, despite being named in older docs:** Etherscan, Dune, GitHub, Snapshot, Tally, Safe API, Token Terminal. Do not document them as present.

CoinGecko calls have exponential backoff on 429 (`RETRY_DELAYS_SECONDS = (2,4,8,16)`) and use `settings.coingecko_api_key` via `_headers()` if set. Demo tier = 30 calls/min vs ~5–15 keyless.

---

## 5. pgvector / RAG — BUILT AND COMPLETELY INERT

This is the third "described vs actual" divergence found in this system. Everything exists:

- `init.sql`: `CREATE EXTENSION vector`, three `embedding vector(1536)` columns (`learnings`, `transcripts`, `knowledge_chunks`), ivfflat cosine indexes
- `knowledge/__init__.py`: `generate_embedding` → OpenAI `text-embedding-3-small` (1536-dim)
- `knowledge/__init__.py`: `semantic_search` with a real cosine query
- `tools/notion.py`: `sync_database_to_pgvector` — chunk + embed + write
- `api/knowledge.py`: `/knowledge` search endpoint, `sync_notion_to_pgvector` endpoint

**But:** `knowledge_chunks` = **0 rows, 0 embeddings**. `learnings` = **0 embeddings**. The sync has never run. No embedding has ever been generated.

**And:** the only memory tool agents have is `search_notes`, which calls `search_notion()` — Notion **keyword** search, limit 5. `semantic_search` is reachable only over HTTP. **No agent calls it.**

So agents retrieve prior context via Notion keyword lookup while a complete parallel semantic retrieval system sits switched off beside them.

### Activation path (if pursued)

1. Verify the OpenAI key is funded and valid (it was empty at one point and caused fallback failures).
2. Hit `sync_notion_to_pgvector`; watch `knowledge_chunks` go 0 → nonzero. Costs cents.
3. **Open question, answer honestly:** at tens of Notion pages, does semantic search actually beat keyword? Benefit may be marginal. Do not assume.
4. Only if worth it: register a `semantic_search` agent tool beside `search_notes` and compare retrieval quality.

**Trap:** do not reimplement. The path exists. Activate it.

There is an uncommitted local fix in tree 2 at `backend/app/knowledge/__init__.py` changing `:embedding::vector` to `CAST(:embedding AS vector)` — correct (avoids SQLAlchemy `::` param-binding conflict), unproven, and in a code path nothing calls. Commit it when pgvector work begins.

---

## 6. ACTIVE WORK ITEM — performance retrospective

**This is what we are doing next.** The question: how did each asset perform relative to how the committee rated it, and what improvements does that yield?

### 6.1 The corpus — VERIFIED, and it is thin

`calibration_records` holds **8 rows**. Two are `INSUFFICIENT_DATA` (failed runs — the 11 Jun Aave attempt and the 18 Jun Plasma Arm A run that died in the temperature/OpenAI cascade). **Six are usable.**

| Project | Ticker | Rec | Score | Chair conf | Entry USD | BTC at entry | Date |
|---|---|---|---|---|---|---|---|
| Aave | AAVE | INSUFFICIENT_DATA | — | unknown | — | 62779 | 2026-06-11 |
| Aave | AAVE | PASS | 77.20 | high | 63.09 | 62964 | 2026-06-11 |
| Plasma | XPL | INSUFFICIENT_DATA | 35.30 | unknown | 0.108772 | 64009 | 2026-06-18 |
| Plasma | XPL | PASS | 34.30 | high | 0.106058 | 63983 | 2026-06-18 |
| GEODNET | GEOD | WATCH | 62.60 | medium | 0.216691 | 64090 | 2026-06-18 |
| Ethena | ENA | WATCH | 53.20 | medium | 0.094421 | 63960 | 2026-06-18 |
| Morpho | MORPHO | WATCH | 65.60 | medium | 1.99 | 63964 | 2026-06-18 |
| Pendle | PENDLE | WATCH | 62.30 | medium | 1.43 | 63889 | 2026-06-18 |

**Zero BUY. Zero VETO. Two PASS. Four WATCH.**

All checkpoint columns (`price_30d/90d/180d`, `return_*`, `alpha_vs_btc_*`, `checked_*_at`) are **NULL**. Nobody has ever run a checkpoint.

### 6.2 What this corpus can and cannot answer

**Cannot:** measure skill. The scorecard's key metric is *discrimination* — did BUYs outperform PASSes. With zero BUYs it is **uncomputable**, not merely weak. Meaningful discrimination needs dozens of resolved cases per bucket.

**Cannot:** draw conclusions from raw return. In a bear market every PASS/WATCH looks correct on price. Only **alpha vs BTC** separates judgment from beta.

**Cannot:** conclude from elapsed time. Entries are 11 and 18 June; today is 24 August = **67 days**. Past the 30d mark, three weeks short of 90d. Mid-cap 30-day moves are mostly BTC correlation.

**Can, and this is the real exercise:** for each of the six, read what the committee *said would matter* (named risks, thesis, stated signposts) and check it against what *actually drove the price over 67 days*. The question is not "was the call right" — a WATCH is unfalsifiable — it is **"did the committee identify the variable that turned out to matter?"** A WATCH on Ethena that flagged funding-rate dependence, where funding rates then drove the move, is a hit. A WATCH that discussed governance and TVL while price moved on an unlock nobody mentioned is a miss, and tells you the committee watches the wrong variables. This works at n=6 and produces actionable improvements.

### 6.3 BLOCKING BUG — do not run the checkpoint endpoint

`knowledge/calibration.py::update_checkpoint(record_id, horizon_days)` — verified by reading the body:

- takes a horizon but **no date**
- calls `_fetch_price(coingecko_id)` → `/simple/price` = **spot only**
- writes `datetime.now(timezone.utc)` to `checked_{N}d_at`
- `return_pct = (current - entry)/entry * 100`
- `alpha_pct = return_pct - btc_return` (simple difference, not ratio)

**Running `/api/calibration/checkpoint/{id}/30` today would write the 24 August spot price into `price_30d` and label a 67-day return as a 30-day return — permanently corrupting the only ledger that exists.** Do not call it.

**Fix required (improvement #1):** checkpoints must fetch price *as of the target date*, not spot. CoinGecko free tier: `/coins/{id}/history?date=DD-MM-YYYY`. Add a date parameter, derive the target date from `entry_captured_at + horizon_days`, and only then is the endpoint safe to run late.

### 6.4 Backfill plan (decision pending from Jacob)

Two options were put to Jacob and **he has not yet answered**:

- **(a)** Backfill `price_30d` properly with true historical prices — 11 July for Aave, 18 July for the five-project cohort, plus BTC on both dates (~7 CoinGecko history calls). Keeps the 30/90/180 series intact.
- **(b)** Skip 30d and compute a one-off 67-day mark-to-market for the retrospective only.

Assistant's lean: **both** — they answer different questions; use `outcome_notes` for the 67-day figure.

**Provenance rule for any backfill:** write the *true observation date* into `checked_30d_at` (i.e. 18 July), not `now()`, and note the backfill in `outcome_notes`. Otherwise a reconstructed checkpoint is indistinguishable from a timely one, which is exactly the class of error this project keeps making.

The genuine 90d checkpoint for the 18 June cohort falls on **16 September 2026** — three weeks out. Fix the date bug before then so it captures correctly on the day.

### 6.5 Structural finding already established (does not need more data)

The committee has **no conviction mechanism**. It can veto but cannot convict; `WATCH` is free and unfalsifiable; its default output is a medium-confidence non-decision. Four of six records are WATCH at 53–66. One high-confidence judgment contradicted its own scoring (Aave). You cannot calibrate a system that never commits.

Deciding whether to add conviction authority, and whether to penalise deferral, is a design decision Jacob must make. It is prerequisite to calibration ever meaning anything.

### 6.6 Missing evaluations — minor, probably benign

`evaluations` has 13 rows; `calibration_records` has 8. `evaluations` is a job-tracking table (`status`, `triggered_by`, `error`, `project_id` → `projects`), not a results table. The five without calibration records most likely predate the calibration wiring (~14 June) or failed. Confirm with:

```sql
SELECT e.status, e.created_at::date, p.name,
       (e.error IS NOT NULL) AS errored,
       (c.id IS NOT NULL) AS has_calib
FROM evaluations e
LEFT JOIN projects p ON p.id = e.project_id
LEFT JOIN calibration_records c ON c.evaluation_id = e.id
ORDER BY e.created_at;
```

If any post-date the wiring and show `status: completed` with no calibration row, capture is silently failing and the hole will grow.

---

## 7. Infrastructure

### 7.1 Machines

| | Detail |
|---|---|
| **Mac** | user `Jacob`, home `/Users/Jacob`, host Python **3.14.5** (too new for pinned deps — do not trust local venv validation) |
| **Mac Tailscale** | `100.118.38.75` |
| **VPS** | Hetzner, Ubuntu 22.04.5 LTS, host Python 3.10.12 |
| **VPS Tailscale** | `100.95.239.105`, node name `tailscale-vps-1` |
| **VPS public IP** | `89.167.61.41` |
| **Container Python** | 3.12 — **this is the only trustworthy runtime for import validation** |
| **Dead node** | `tailscale-vps` @ `100.85.27.99`, offline 111+ days. Ignore; delete from the Tailscale admin console when convenient. |

SSH: `ssh root@100.95.239.105`

### 7.2 Containers

`committee-backend` (8100), `committee-postgres` (pgvector/pgvector:pg16, 5432), `committee-redis` (redis:7-alpine, 6379). All `restart: unless-stopped`.

Volumes: `committee-orchestrator_pgdata`, `committee-orchestrator_redisdata` — **named volumes, external to the repo directory**. This was verified before the re-clone; it is why re-cloning the repo did not touch the database. Preserve that property.

Backend bind-mounts `./backend/app:/app/app` and runs uvicorn with WatchFiles, so **Python changes hot-reload without a restart**. Compose changes require `docker compose up -d`.

### 7.3 Deployment pipeline (built and proven this session)

```
edit on Mac (~/Projects/aiic) → commit → push to GitHub → git pull on VPS
```

Proven twice under real conditions. `git pull --ff-only` on the VPS is the entire deploy.

For persona-only fast iteration there is also a Mac alias:

```bash
sync-committee   # rsync -av --include='*/' --include='*.md' --exclude='*' \
                 #   ~/Projects/aiic/backend/app/memory/ \
                 #   root@100.95.239.105:/opt/committee-orchestrator/backend/app/memory/
```

**Markdown-only by design.** An earlier unscoped rsync overwrote `agent_personas.py` and `__init__.py` on the live box. It happened to be harmless; it might not be next time. Never sync Python this way.

---

## 8. Credentials

`.env` lives **only** on the VPS at `/opt/committee-orchestrator/.env` (1248 bytes). It is gitignored and that was verified with `git check-ignore` after the re-clone. A backup exists at `~/aiic-vps-env.backup` on the Mac.

Required / present:

| Key | Purpose | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | all agent calls | primary |
| `OPENAI_API_KEY` | **embeddings only** (`text-embedding-3-small`) | was empty at one point, causing cascade failures; verify funded before pgvector work |
| `POSTGRES_PASSWORD` | database | **required** — compose uses `${POSTGRES_PASSWORD:?...}` and fails fast if unset. Value is NOT `committee_dev_pw` (md5 confirmed different). It matches what the volume was initialised with — do not change it or postgres will reject connections. |
| `BRAVE_SEARCH_API_KEY` | `web_search` | |
| `NOTION_API_KEY` + `notion_transcripts_db`, `notion_learnings_db`, `notion_projects_db` | `search_notes`, `read_note`, writeback | |
| `COINGECKO_API_KEY` | optional, demo tier 30/min | wired via `settings.coingecko_api_key` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID` | optional Telegram surface | |

### 8.1 Key rotation — STANDING OPEN ITEM, NEVER DONE

`.env` was previously tracked in the VPS repo's local git history at commit `52d6fc1`. That history had **no remote and was never pushed**; GitHub was verified clean of `.env` across all commit trees. So it was latent local exposure, not a confirmed public leak.

That dirty history was destroyed when `/opt/committee-orchestrator.old` was removed during reconciliation, so the git-borne exposure is gone.

**However:** keys have been visible in pasted terminal output and screenshots across sessions. Rotation of `ANTHROPIC`, `OPENAI`, `BRAVE`, `NOTION` remains a prudent, unresolved item. It is a judgment call, not a mandate.

**If `POSTGRES_PASSWORD` is ever rotated**, the existing Postgres volume will reject the new password — it is baked into the data directory at init. Rotating it requires `ALTER USER` inside the running database, not just an `.env` edit.

---

## 9. Known issues and failure modes

### 9.1 Access failures (these consumed most of the last session)

- **NordVPN over Tailscale blackholes SSH.** Signature: `tailscale ping` returns pong (rides DERP relays) but `ssh` times out with `Operation timed out`. Cause: full-tunnel commercial VPN hijacks the default route. **Fix: quit NordVPN.** This recurred multiple times.
- **Tailscale stopped.** `tailscale ping` returns `Tailscale is stopped.` — start it.
- **Login banner eats chained commands.** The Ubuntu MOTD swallows commands chained onto a fresh `ssh` invocation. Run one block at a time after the prompt settles, or wrap commands in `ssh host "..."`.
- **Wrong-machine commands.** The single largest time sink. `/opt/committee-orchestrator` exists only on the VPS; `~/Projects/aiic` only on the Mac; `/private/tmp/aiic-*` staging dirs were Mac-only. Check the shell prompt for `ssh tailscale-vps` before every command. Symptoms of running on the wrong box include `zsh: no matches found` (zsh on Mac vs bash on Ubuntu) and empty grep results that look like passing checks.

**Standing preflight, run at the start of every VPS session:**

```bash
tailscale ping 100.95.239.105
ssh root@100.95.239.105 "echo REACHABLE && docker compose -f /opt/committee-orchestrator/docker-compose.yml ps"
```

### 9.2 Container limitations

`committee-backend` has **no** `ps`, `pkill`, `gh`, or `gitleaks`. To kill an in-container process use `docker compose restart backend`. To enumerate processes use `/proc`.

### 9.3 Editing `.env` requires `--force-recreate`

`docker compose restart backend` **reuses baked-in environment variables**. After any `.env` change you must:

```bash
docker compose up -d --force-recreate backend
```

This cost two full sessions before it was understood.

### 9.4 `init.sql` only runs on a fresh volume

`calibration_records` is defined in `backend/init.sql`, which Postgres executes **only** when initialising an empty data directory. On an existing volume the table must be created by hand. It currently exists (verified), but any future schema addition has the same trap.

### 9.5 Historical breakages, all fixed — do not reintroduce

| Failure | Symptom | Fix |
|---|---|---|
| Dead model strings | 404 on every call | tiers now resolve from `settings` in `llm/claude.py::_resolve_model` |
| `temperature` param | 400 `temperature is deprecated for this model` | removed from Claude kwargs |
| OpenAI fallback masking | Claude errors hidden behind empty-quota 429s | **the GitHub `router.py` has no fallback at all** — it selects one provider at init. Better than the old VPS version. Do not add a try/except fallback back in. |
| CoinGecko 429 storms | agents stall, thin data | backoff + optional key |
| `read_note` slug 404s | agents pass `"geodnet-prior-eval"` as page_id | UUID regex guard, returns graceful no-note |
| Jaccard risk overlap = 0.000 | impossible metric in concordance run | mismatched extraction shapes; fixed but never revalidated by rerun |
| `docker-compose.yml` YAML | `mapping values are not allowed` | **two** lines had corrupted indentation (`POSTGRES_PASSWORD` line 8, `DATABASE_URL` line 44) — both were the lines edited when hardening `:-default` → `:?error`. Fixed in `bdad5cd` and `8432cf4`. |

### 9.6 Cosmetic / low priority

- `agents/orchestrator.py` docstring line 11 still says **"Ray Munger"** — fossil of the Charlie Munger → Ray Dalio rename. Class is `RayDalio`. Comment only.
- `.DS_Store` files were rsynced onto the VPS persona directories by the early unscoped sync. Harmless; removed by re-clone.
- **9 Dependabot vulnerabilities** on the public repo: 1 critical, 3 high, 2 moderate, 3 low. `https://github.com/polski-sklep/aiic/security/dependabot`. Unaddressed.
- VPS has 29 pending apt updates, 3 ESM security updates, `*** System restart required ***`, and 1–2 zombie processes. Ubuntu 24.04.4 LTS upgrade available.
- README was stale (13 agents, "only Tokenomics built"). A rewrite was produced in the last session; **verify whether it was committed** — if not it is at `/mnt/user-data/outputs/README.md` from that session or must be regenerated.

---

## 10. The concordance experiment — result banked, not acted on

**Question:** do 8 specialist data agents change the decision versus 1 generalist?

**Method:** `concordance_harness.py` ran two arms per project. Arm A = full 15-agent pipeline. Arm B = `lean_orchestrator.py` subclassing the production Orchestrator, replacing the 8 data agents with a single `generalist_analyst.py`, every downstream component unchanged so the *only* variable was 8 calls vs 1.

**Result, 5 mid-caps:** recommendation match **80% (4/5)**. Agreed PASS/PASS on Plasma; WATCH/WATCH on GEODNET, Morpho, Pendle. Diverged only on **Ethena** (Arm A WATCH, Arm B VETO). Average score delta 8.88.

**Contamination, stated honestly:** CoinGecko 429s degraded nearly every call; risk-overlap metric was buggy (0.000, discard); Jaccard fixed afterwards but **never revalidated by a rerun**. Cost ~$40 total.

**Jacob's decision: do not collapse yet. Bank the result, decide later.** Respect that.

**Code location:** `generalist_analyst.py`, `lean_orchestrator.py`, `concordance_harness.py` are **NOT in the repo by deliberate choice**. They are archived at `~/aiic-vps-snapshot-20260708/` on the Mac. Rationale: single-use measurement instrument, known-unvalidated bugfix, no product role. Copy back from the snapshot if the collapse question is ever revisited.

---

## 11. Risk Officer redesign — IN PROGRESS, decisions made, 4 questions open

A redesign of the `risk-officer` persona was underway when the session ended. The existing files are internally contradictory: SOUL says veto when "evidence is too weak", TOOLS says "do not issue veto from intuition alone when observable evidence can be checked". Same situation, opposite instructions.

### Decisions Jacob has made (settled)

1. **Veto fires on presence-of-danger only, never absence-of-evidence.** Missing information produces a *flag*, not a stop.
2. **The veto protects against being TRAPPED, not against being WRONG.** Specifically: mechanism-irrecoverable (funds frozen/seizable by design) and liquidity-irrecoverable (cannot exit at depth). **Thesis-death — value goes to zero but exit is clean — is explicitly NOT the veto's job.** That is the rest of the committee's job.
3. **Liquidity vetoes only at the degenerate end** — when depth fails at *any* size. Otherwise it is a sizing constraint passed to the Portfolio Manager, since the officer does not set position size.
4. **Architecture = hybrid.** A closed list of automatic veto conditions, PLUS an open clause requiring a named verified fact and a stated mechanism from that fact to irrecoverable loss. Open-clause vetoes are tagged as such, reviewed separately, and recurring mechanisms get promoted onto the closed list. The closed list is the settled output of the open clause over time.

Rationale for hybrid: a closed list alone is a list of the last cycle's failures and misses novel mechanisms (Terra, FTX, bridge exploits were all novel at the time). An open standard alone is discretion with better grammar — it drifts and cannot be reviewed.

### Candidate closed-list conditions

Jacob said "all of them" to these six, but four still need thresholds:

| # | Condition | Status |
|---|---|---|
| 1 | Unaudited contract holding user funds | **Needs floor** — without one this vetoes almost every mid/low cap. Proposal: veto if unaudited AND it is the contract your funds enter; else flag. |
| 2 | Upgradeable contract, unbounded/single-key admin | **Needs timelock threshold.** Proposal: no timelock or <24h → veto; ≥24h with public upgrade queue → severe flag (exit is possible). |
| 3 | Single unbonded custodian of user assets | Clean automatic veto |
| 4 | Uncapped / mutable mint authority | **Needs narrowing.** Proposal: veto only on live, uncapped, single-key, no-timelock; renounced/timelocked/capped → flag. |
| 5 | No withdraw path / one-way deposit | Clean automatic veto, definitional |
| 6 | Verified prior rug by same team | **Needs evidentiary standard.** This is a prediction about people, not a mechanism, and identity attribution in crypto is often contested. Proposal: auto-veto on *verified* only (on-chain link or doxxed/admitted); credible allegation → severe flag. Note it is unfalsifiable for calibration purposes in a way the others are not. |

**Open questions to put to Jacob before writing the files:** the four thresholds above.

Then: one liquidity condition, the open-clause evidentiary bar, and `SOUL.md` / `CONSTRAINTS.md` / `INTERFACES.md` / `TOOLS.md` get rewritten from these decisions.

### Broader persona plan

Jacob wants all 15 agents' `SOUL`, `CONSTRAINTS`, `INTERFACES` and where present `TOOLS` fleshed out, one agent at a time, via detailed interrogation before writing. Sixty documents total. Recommended order: `risk-officer` first (veto scope bounds everything else), then `devils-advocate` (most likely to be malformed — an adversary that is "balanced" is not an adversary).

**Two constraints on this work:**
- These files become runtime context on paid API calls. Longer is not better — an agent with a three-page SOUL follows it less faithfully than one with six sharp lines, because the salient instruction gets buried.
- Sharpening objectives makes agents more distinct (and may reveal some should be deleted). Adding domain knowledge and voice makes them richer and *more similar*. The concordance result suggests the current set already suffers from insufficiently distinct objectives — flesh out the objective axis, not the knowledge axis.

---

## 12. Obsidian integration (built, working)

Jacob authors persona files in Obsidian inside his **main** vault, not a separate one.

- Main vault: `/Users/Jacob/Workspace/vault`
- Symlink: `/Users/Jacob/Workspace/vault/04-Projects/AI/committee-orchestrator/aiic-memory` → `/Users/Jacob/Projects/aiic/backend/app/memory`
- `.obsidian/` is in `~/Projects/aiic/.gitignore`

The symlink means editing in Obsidian edits the repo file directly — **one source of truth, not a copy**. Obsidian is a viewer; it has no runtime role. The committee reads files from disk on the VPS after `git pull` or `sync-committee`.

Note: `04-Projects/AI/committee-orchestrator/` also contains Jacob's *own notes* about the project (AIIC Overview, Architecture, Data Model, Decisions Log, Roadmap). Those are **not** the persona files and have no runtime effect. Keep the two conceptually separate; do not merge them.

---

## 13. Working files and artifacts on the Mac

| Path | Contents |
|---|---|
| `~/Projects/aiic` | canonical editing clone |
| `~/Projects/committee-orchestrator` | **abandoned** tree 4, dirty, contains uncommitted cleanup work (§0) |
| `~/aiic-vps-snapshot-20260708/` | full VPS `backend/app` snapshot incl. the concordance trio |
| `~/aiic-vps-env.backup` | `.env` backup, 1248 bytes |
| `~/aiic-vps-db-20260708.sql.gz` | Postgres dump, 552 KB |
| `~/setup_aiic_vault.sh` | vault setup script |
| `~/link_personas.py` | symlink creation script |

Uncommitted in `~/Projects/aiic`: `.gitignore` (the `.obsidian/` line — harmless) and `backend/app/knowledge/__init__.py` (the `CAST(... AS vector)` fix — see §5).

---

## 14. Methodology — the rules this project learned the hard way

These are not platitudes; each was paid for.

1. **Verify with the instrument that consumes the artifact.** `compileall` never reads `docker-compose.yml`, so it certified a compose file that had been broken in two places for weeks. Docker parses compose — use `docker compose config -q`. On the Mac that will fail on the missing `POSTGRES_PASSWORD`; use `--no-interpolate` for a pure syntax check, or validate on the VPS.

2. **Testing for the presence of a fix ≠ testing for the absence of the problem.** Three "MISSING" regressions were found by grepping for specific fix-text; all three were false alarms because the rebuild had implemented the same fix with different wording. Read the function.

3. **Unverified means unknown, not true.** Three capabilities in this system were described accurately in docs and absent in the runtime (agent count, `.env` hygiene, pgvector retrieval). Change the default state of an unchecked claim from "true" to "unknown". Verify at the point of consequence.

4. **Compile ≠ import ≠ runs.** A module can compile and still ImportError. Validate imports in the **container's** Python 3.12, never the Mac's 3.14: `docker compose run --rm backend python3 -c "import app.main"`.

5. **Never trust `git status` alone for secrets.** Use `git check-ignore <path>` — it is definitive. Inspect `git diff --cached --name-only` before every commit.

6. **Port onto the newer base; never copy older files over newer architecture.** A naive deploy nearly deleted `prompt_utils.py`, `tools/registry.py`, `utils/citations.py`, `utils/types.py` — all load-bearing on `main`, absent from the VPS tree. It compiled cleanly and would have been a silent feature rollback.

7. **Preserve rollbacks until every check is green.** `/opt/committee-orchestrator.old` was kept through the entire re-clone and only deleted after the stack came up healthy with data intact.

---

## 15. Immediate next steps, in order

1. **Triage tree 4** (§0). Decide what of the eight cleanup passes to port onto `aiic`. Do not resume work in `committee-orchestrator`.
2. **Resolve the persona-slug → agent-class mapping** (§2.1) by reading `agent_personas.py`. Everything persona-related depends on it.
3. **Fix the checkpoint date bug** (§6.3) before 16 September, when the real 90d checkpoint falls due.
4. **Get Jacob's backfill decision** (§6.4), then backfill with true historical prices and correct provenance.
5. **Run the qualitative retrospective** (§6.2) — the actual active work item. Six reports, 67-day price action, does the committee's stated reasoning match what moved the price.
6. Resume the Risk Officer persona work (§11) with the four open thresholds.

Everything else — pgvector activation, conviction mechanism, Dependabot, VPS updates — is downstream of these.
