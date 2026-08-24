# Shared contracts

Binding interfaces between parallel workstreams. **No agent may change anything
in this document unilaterally.** If your work requires a change here, stop and
report to the orchestrator.

Base commit for all agent branches: `5d3c033` on `integration`.

---

## 1. File ownership

Each path is owned by exactly one branch. Do not edit files you do not own — if
you find a defect outside your scope, write it into your report and let the
owner fix it.

| Branch | Owns |
| --- | --- |
| `agent/architecture` | `backend/app/tools/contracts.py`, `backend/app/tools/registry.py`, `backend/app/tools/*.py` import lines, `docs/adr/**`, `docs/triage-tree4.md` |
| `agent/calibration` | `backend/app/knowledge/calibration.py`, `backend/app/api/calibration.py`, `backend/scripts/**`, `backend/tests/test_calibration.py` |
| `agent/persistence` | `backend/app/models/__init__.py`, `backend/app/api/evaluate.py`, `backend/app/agents/orchestrator.py`, `backend/app/database.py`, `backend/init.sql`, `backend/migrations/**` |
| `agent/personas` | `backend/app/memory/**` (both `.md` and `agent_personas.py`) |
| `agent/retrospective` | `docs/retrospective/**` — analysis only, no code |
| `agent/devops` | `.github/**`, `backend/Dockerfile`, `docker-compose*.yml`, `backend/requirements*.txt`, `Makefile`, `.env.example`, `pyproject.toml`, `.gitleaks.toml` |
| `agent/security` | `docs/reviews/security-review.md` — report only, no code fixes |
| `agent/qa` | `backend/tests/**` **except** `test_calibration.py` |
| `agent/ui-report` | `backend/app/tpl.html`, `backend/app/api/reports.py`, `backend/app/static/**` |
| `agent/retrieval` | `backend/app/knowledge/__init__.py`, `backend/app/api/knowledge.py`, `backend/app/tools/semantic.py`, `docs/reviews/retrieval-evaluation.md` |
| `agent/core` | `backend/app/main.py`, `backend/app/agents/orchestrator.py`, `backend/app/agents/chair.py`, `backend/app/utils/types.py`, `backend/app/api/memory.py`, `backend/migrations/0003_*.sql` |
| orchestrator only | `README.md`, `AGENTS.md`, `PROJECT_DECISIONS.md`, `docs/CONTRACTS.md`, `AIIC_HANDOFF.md`, `.gitignore` |

---

## 2. Verified facts every agent must build on

These were checked against the live runtime on 24 Aug 2026. Several **contradict
the handoff brief** — the runtime wins.

### 2.1 Persona map — RESOLVED, and it hid two defects

`backend/app/memory/agent_personas.py::AGENT_FOLDERS` is the authoritative
class→folder map. Handoff §2.1 is resolved. Both defects it concealed are fixed
on `integration` (merge of `agent/personas`):

- `tech_infra_analyst` was **absent from the map**. One of two agents carrying
  the joint-highest score weight (0.15) ran with no persona at all —
  `load_agent_persona` returned `""` and `BaseAgent` fell back to its
  one-paragraph `role_description`. It now has a five-file persona.
- `knowledge-agent/` was an orphan mapped to no class. Archived under
  `backend/app/memory/archive/`, not deleted — it is the ready-made spec if the
  retrieval layer ever gets a seat.

Current state, verified in the container: **15 agents mapped, 15 folders, no
`MISSING`, one-to-one.**

### 2.1a Data-agent independence was being contradicted at runtime

`docs/CONTRACTS.md` §4.2 and the handoff both state the eight data agents must
not see each other's output — that diversity is the design. **Six of the eight
personas were telling the agents the opposite**, listing sibling data agents
under "Receives From" and offering their output as available inputs. This was
live on every paid call.

Fixed. It bears directly on the concordance result in handoff §10 (80%
recommendation match between eight specialists and one generalist): personas
that instruct agents to converge are a plausible partial cause of measured
convergence. The experiment's conclusion should not be treated as settled while
that contamination is unaccounted for.

### 2.2 pgvector is NOT inert — the handoff §5 is wrong

```
knowledge_chunks: 62 rows, 62 embeddings
learnings:         0 rows,  0 embeddings
```

The Notion→pgvector sync **has run**. What remains true from §5 is that
`semantic_search` is reachable only over HTTP and **no agent calls it**; the
only retrieval tool agents have is `search_notes` (Notion keyword, limit 5).

### 2.3 The `reports` table is dead schema

`reports` has **0 rows**. `api/evaluate.py` never constructs a `Report`.
`api/reports.py` rebuilds markdown from `agent_outputs` at request time. The
table, its indexes and its FK exist and are written by nothing.

### 2.4 Calibration records are orphaned from their evaluations

`orchestrator.evaluate()` calls `record_calibration(evaluation_id=None, ...)` —
hardcoded. All 8 rows in `calibration_records` have `evaluation_id IS NULL`.
There is no join from the calibration ledger to the reasoning that produced it.

### 2.5 The 18 June cohort has no local reasoning at all

`evaluations` holds 13 rows: nine from April, two from 1 June, two from 11 June.
The five-project 18 June cohort (Plasma, GEODNET, Ethena, Morpho, Pendle) was run
by `concordance_harness.py` **calling the Orchestrator directly**, bypassing the
API. So it produced calibration rows but **no `evaluations`, no `agent_outputs`,
no report**. `concordance_results.json` was written inside the container and lost
when the container was rebuilt on 9 July.

**The reasoning survived in exactly one place: the Notion projects database.**
Each project page body holds ~9 KB of per-agent summaries plus the top five
deduplicated risks, written by `orchestrator._notion_write`. Verified page ids:

| Project | Notion page id |
| --- | --- |
| Aave | `33f0a58c-96ec-8144-b025-e19f9b179c00` |
| Plasma | `3830a58c-96ec-8123-a384-d8f217a43a6e` |
| GEODNET | `3830a58c-96ec-811f-815e-c529f31161c4` |
| Ethena | `3830a58c-96ec-8116-80ba-cb7ee0933c71` |
| Morpho | `3830a58c-96ec-8162-8c35-f6cd3cb04b6a` |
| Pendle | `3830a58c-96ec-8170-a0dc-cfd96f5314e7` |

### 2.7 A CoinGecko 429 is indistinguishable from missing data

Verified by direct probe on 24 Aug 2026. The free tier returns **HTTP 200** with
no `market_data` key when rate-limited:

```json
{"status":{"error_code":429,"error_message":"You've exceeded the Rate Limit. ..."}}
```

Code that branches on `if "market_data" not in data` will record a rate limit as
"the coin did not exist on that date". Any consumer must check `status.error_code`
**before** checking `market_data`, and must distinguish three outcomes — price
found, no data for that date, fetch failed — never collapsing the last two.

The limit is tighter than expected: 429 on the **fourth** call at 8-second
spacing. Sleep 15–20s and cache responses to disk.

The historical endpoint itself works keyless and is confirmed good:
`GET /coins/{id}/history?date=DD-MM-YYYY&localization=false` →
`market_data.current_price.usd`.

### 2.6 Live calibration ledger (do not mutate without orchestrator approval)

| Project | id | Rec | Score | Conf | Entry USD | BTC entry | Date |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Aave | `89b57672…` | INSUFFICIENT_DATA | — | unknown | NULL | 62779 | 2026-06-11 |
| Aave | `df935a82…` | PASS | 77.20 | high | 63.09 | 62964 | 2026-06-11 |
| Plasma | `972cacc2…` | INSUFFICIENT_DATA | 35.30 | unknown | 0.108772 | 64009 | 2026-06-18 |
| Plasma | `5a297bf6…` | PASS | 34.30 | high | 0.106058 | 63983 | 2026-06-18 |
| GEODNET | `815e976e…` | WATCH | 62.60 | medium | 0.216691 | 64090 | 2026-06-18 |
| Ethena | `012d451c…` | WATCH | 53.20 | medium | 0.094421 | 63960 | 2026-06-18 |
| Morpho | `b28e816a…` | WATCH | 65.60 | medium | 1.99 | 63964 | 2026-06-18 |
| Pendle | `65938079…` | WATCH | 62.30 | medium | 1.43 | 63889 | 2026-06-18 |

All `price_*`, `return_*`, `alpha_*`, `checked_*_at` are NULL. CoinGecko ids are
the lowercased names: `aave`, `plasma`, `geodnet`, `ethena`, `morpho`, `pendle`.

A gzipped dump taken before any of this work is at
`~/aiic-backups/aiic-db-backup-20260824.sql.gz` (Mac) and
`/root/aiic-db-backup-20260824.sql.gz` (VPS).

---

## 3. Interface contracts

### 3.1 `record_calibration` — signature is FROZEN

```python
async def record_calibration(
    evaluation_id: str | None,
    project_name: str, ticker: str, coingecko_id: str, category: str,
    recommendation: str, overall_score: float | None,
    chair_confidence: str, vetoed: bool,
) -> str | None
```

`agent/calibration` owns the body. `agent/persistence` owns the call site and
must start passing a real `evaluation_id`. Neither may change the signature.

### 3.2 `update_checkpoint` — signature CHANGES, shape agreed here

The current function writes spot price into a dated column. Replacement contract:

```python
async def update_checkpoint(
    record_id: str,
    horizon_days: int,          # 30 | 90 | 180
    as_of: date | None = None,  # default: entry_captured_at + horizon_days
) -> dict[str, Any]
```

Rules:
- Price MUST be fetched **as of the target date**, never spot, via CoinGecko
  `/coins/{id}/history?date=DD-MM-YYYY`. BTC benchmark likewise.
- `checked_{N}d_at` MUST be written with the **true observation date**, never
  `now()`.
- A backfilled checkpoint MUST be marked as such in `outcome_notes`.
- If the target date is in the future, return an error and write nothing.

HTTP: `POST /api/calibration/checkpoint/{record_id}/{horizon_days}` keeps its
path; add optional `?as_of=YYYY-MM-DD`.

### 3.3 Schema additions

`backend/init.sql` runs **only on an empty data directory**. Any new column or
table MUST also ship as a migration that is safe to apply to the existing
volume. `agent/persistence` owns the migration mechanism; every other agent
needing schema requests it through the orchestrator.

New columns agreed for `calibration_records` (owned by `agent/persistence`,
consumed by `agent/calibration`):

```sql
outcome_notes  text
```

### 3.4 Error response format

FastAPI default: `{"detail": "..."}` with the appropriate status code. Do not
invent a new envelope. Never put an exception string into `detail` for a 500 —
log it, return a generic message (`api/evaluate.py` already does this correctly).

### 3.5 Environment variables

Names are fixed by `backend/app/config.py::Settings`. Adding a setting means
adding it there **and** to `.env.example`. Never read `os.environ` directly for
config; go through `get_settings()`.

### 3.6 Tool registration

Tools register through `ToolRegistry.register(definition, func)` from a
module-level `register(registry)` function, wired in
`tools/registry.py::_register_all_tools`. Eleven tools are live:
`get_price`, `get_token_info`, `get_tvl`, `get_protocol_fees`, `get_klines`,
`get_orderbook_depth`, `compute_technical_levels`, `web_search`,
`search_twitter`, `search_notes`, `read_note`.

Etherscan, Dune, GitHub, Snapshot, Tally, Safe and Token Terminal are **not
built**. Do not document them as present.

---

## 4. Load-bearing constraints — violating these is a defect

1. **Technical Analyst never influences conviction.** It is in
   `exclude_from_scores`. Its output reaches the Chair only as
   `technical_entry_context`. Any scoring change must preserve this.
2. **Data agents are independent.** The eight parallel agents must not see each
   other's output. That diversity is the design.
3. **No LLM provider fallback.** `llm/router.py` selects one provider at init.
   Do not add a try/except fallback — it previously masked Claude errors behind
   OpenAI quota 429s.
4. **No `temperature` in Claude kwargs.** It is rejected by the current models.
5. **`POSTGRES_PASSWORD` must not be rotated in `.env` alone** — it is baked
   into the initialised Postgres volume.
6. **`sync-committee` rsyncs markdown only.** Never sync Python to the VPS.
7. **Deploy is `git pull --ff-only` on the VPS.** No other path.

---

## 5. Verification standard

A change is "verified" only if checked with the instrument that consumes it:

| Artifact | Instrument |
| --- | --- |
| Python imports | `docker compose run --rm backend python3 -c "import app.main"` (3.12) |
| `docker-compose.yml` | `docker compose config -q` (`--no-interpolate` on the Mac) |
| SQL | executed against Postgres, not read |
| Tests | actually run, output pasted into your report |

Host Python on the Mac is **3.14.5** and too new for the pinned dependencies.
Local validation in a host venv proves nothing. Use the container.

Never report a check as passing if you did not run it.
