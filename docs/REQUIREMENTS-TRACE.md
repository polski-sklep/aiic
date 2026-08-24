# Requirement → implementation trace

Every requirement derived from `AIIC_HANDOFF.md`, mapped to what implements it
and how it was verified. Status is **only** marked done where a check was
actually run and its output seen.

Legend: **DONE** verified · **PARTIAL** landed, gated on something named ·
**OPEN** not started · **JACOB** needs a decision only he can make.

---

## A. The handoff's ordered work list (§15)

| # | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| 1 | Triage tree 4; decide what to port | `docs/triage-tree4.md`; pass 4 ported as `tools/contracts.py` | AST import-graph scan, 6 latent cycles → 0; `import app.main` in 3.12 | **DONE** |
| 2 | Resolve persona-slug → agent-class mapping | `AGENT_FOLDERS` was already definitive; it concealed two defects, both fixed | `list_personas()` in container: 15 mapped, 15 folders, no `MISSING` | **DONE** |
| 3 | Fix the checkpoint date bug before 16 Sept | `knowledge/calibration.py` rewritten; date-anchored, refuses future dates | 74 tests pass in 3.12 container | **DONE** |
| 4 | Backfill with true historical prices and correct provenance | `backend/scripts/backfill_checkpoints.py`, `--dry-run` default | Dry-run executed against the 8 real production rows restored into a local stack; figures cross-validate with the retrospective and an independent probe | **DONE — production write awaiting Jacob** |
| 5 | Run the qualitative retrospective | `docs/retrospective/**` | 4 documents; 1 HIT, 2 PARTIAL, 2 MISS, 1 UNRESOLVED; every number traceable to a cached source file | **DONE** |
| 6 | Risk Officer persona work, 4 open thresholds | `risk-officer/` rewritten; D4 thresholds; `risk_policy.md` and `risk_officer.py` aligned | Persona loads at 9754 chars; one veto list reaches the agent where three conflicting ones did | **DONE** |

## B. Defects found that the handoff did not know about

| Requirement | Implementation | Verification | Status |
|---|---|---|---|
| `tech_infra_analyst` (0.15 weight) had **no persona at all** | `tech-infra-analyst/`, 5 files, added to the map | 0 → 8547 chars in container | **DONE** |
| Six of eight data-agent personas **contradicted the independence design** | Cross-agent input claims removed | Persona diff reviewed | **DONE** |
| `reports` table was dead schema — 0 rows, written by nothing | `_persist_report` in `api/evaluate.py`, additive versioning | Version increment 1→2→3 with veto fields round-tripping | **DONE** |
| Calibration records orphaned — `evaluation_id` hardcoded `None` | Threaded through `Orchestrator.evaluate()`; **commit before pipeline** | `record_calibration` returns an id and the row links; without the commit it returns `None` | **DONE** |
| `init.sql` only runs on an empty volume — schema changes never reach production | Forward-only migration runner, advisory lock, checksum ledger | Applied to a volume seeded to match production; rows intact | **DONE** |
| Semantic similarity threshold 0.7 vs a model that tops out at 0.63 — retrieval returned nothing while being correct | Lowered to 0.30, derived from measured noise ceiling | 14 queries return results; all 9 local checks pass | **DONE** |
| Notion→pgvector sync duplicated all 62 chunks on re-run | Endpoint guard, `replace=true` required | Second press skips | **DONE** |
| `semantic_search` had never executed — `::vector` binding bug | `CAST(... AS vector)`, committed as `5d3c033` | Ran clean against real embeddings | **DONE** |
| Registered tools are invisible unless listed in `_base_tools` | `semantic_search_notes` added to registry **and** base tools | 12 tools registered; tokenomics sees it | **DONE** |
| Chair decides **before** the weighted score exists | Score computed first; divergence and contradiction recorded separately | Invariance proven over 2000 randomised result sets, 0 mismatches | **DONE** |
| Chair's 24-section report sliced to 6000 chars mid-JSON | Budgeted per-section formatter | Was 9/24 sections and 0/11 decision fields; now 24/24 and 11/11 at every section size | **DONE** |
| `WATCH` unfalsifiable — Chair emits `signposts`, ledger discards them | Migration 0003 + `record_signposts`, wired into the orchestrator | Round-tripped against Postgres: `jsonb_array_length` 2, `review_date` a real date | **DONE** |
| `/openapi.json` and `POST /api/tools/{name}` 500 on an unrebuilt Pydantic forward-ref | `JSONValue` became a `TypeAliasType`; tool execution deliberately gated at 403 | `/openapi.json` 200, `/docs` 200 | **DONE** |
| `/api/projects/{id}` 500s on non-UUID instead of 422 | Path parameter typed `UUID` | Returns 422 | **DONE** |
| Stored XSS: `</script>` breaks out of the report page and runs JS | Escape-first server-side renderer; no JS in the page; CSP `default-src 'none'` | Payload replayed end-to-end and parsed with `html.parser`: 0 script elements, 0 img elements, 0 event handlers | **DONE** |
| `marked` from CDN, unpinned, no SRI | Renderer written in Python; no new dependency | One request per page load; renders offline | **DONE** |
| `ufw` inactive, `INPUT` policy `ACCEPT`, all services on `0.0.0.0` | Postgres and Redis bound to `127.0.0.1`; host rules written up including `DOCKER-USER` | Compose validated; host commands **not run** — they change Jacob's live server | **DONE in repo / JACOB on the host** |

## C. Non-functional requirements

| Requirement | Implementation | Status |
|---|---|---|
| Tests exist and run | 272 tests, stdlib `unittest`, hermetic (httpx mocked, sockets blocked) | **DONE** |
| CI | 2 workflows, 6 jobs, built around this project's actual failure history | **DONE** |
| Lint / type checking | ruff clean; mypy clean over 50 source files; debt ledgered, not hidden | **DONE** |
| Migrations work on the live volume | Runner + `0001`/`0002` proven on a populated volume | **DONE** |
| Documentation | README corrected on 5 counts; `docs/operations.md`, 2 ADRs, 4 review documents, retrospective | **DONE** |
| `.env.example` accurate | Rewritten; dead variables quarantined with read counts | **DONE** |
| Dependabot: 1 critical, 3 high, 2 moderate, 3 low | `python-jose` removed (the critical, and unimported); `python-multipart` bumped | 21 advisories to 7, none reachable; `pip-audit` clean | **DONE** |
| Secrets never committed | `.gitleaks.toml` extended to Anthropic/OpenAI/Brave/CoinGecko/Notion keys | `gitleaks detect`: no leaks found | **DONE** |
| No production mutation without review | Nothing written to the VPS; `pg_dump` taken first | **DONE** |

## D. Decisions only Jacob can make

| Question | Where it is set out | Status |
|---|---|---|
| Score/chair coherence: keep cardinal + forced reconciliation, ordinal conviction tiers, or pairwise reference cases | `docs/adr/0002-score-chair-coherence.md` | **JACOB** |
| Conviction mechanism — should the committee be able to *convict*, and should deferral be penalised (§6.5) | ADR 0002 + retrospective findings | **JACOB** |
| Should the Chair be able to override a veto? Documented as possible; not implemented (D10) | `PROJECT_DECISIONS.md` D10 | **JACOB** |
| API key rotation (§8.1) — needs his accounts | `docs/reviews/security-review.md` SEC-10 | **JACOB** |
| Host firewall rules on the live VPS | `docs/operations.md` | **JACOB** |
| Collapse 8 data agents to 1 generalist? Banked, not decided — and the independence defect is new evidence | Handoff §10 + `docs/CONTRACTS.md` §2.1a | **JACOB** |
| `maturation_scorer` vs `valuation-scorer` seat mismatch — persona prices, class scores maturity | Personas report | **JACOB** |

## E. Explicitly out of scope

- **No Next.js frontend** (D7). Not in the work list; the HTML report and the
  Telegram bot are the human surfaces.
- **No deploy** (D8). Integration is local; the deploy commands are in the
  handover for Jacob to run.
- **Backgrounding the evaluation pipeline.** `POST /api/evaluate` runs fifteen
  agents synchronously. `arq` and Redis are both present and unused. Assessed
  and deliberately deferred: it is a response-contract change that breaks
  `telegram_bot.py` without a matching poll loop, and it wants its own review
  rather than riding along with schema and persistence changes.

---

## F. Final review — what was actually run, on `main`, after integration

Every line below was executed and its output seen. Nothing here is inferred.

| Check | Command | Result |
|---|---|---|
| Compose syntax | `docker compose config -q --no-interpolate` | PASS |
| Compose with env | `docker compose config -q` | PASS |
| Clean image build | `docker compose build --no-cache backend` | PASS |
| Imports, container 3.12 | `python3 -c "import app.main"` | PASS |
| Test suite | `python3 -m unittest discover -s tests` | **272 tests, OK**, 6 expected failures |
| Lint | `make lint` (ruff) | All checks passed |
| Types | `make typecheck` (mypy) | No issues in 50 source files |
| Dependencies | `make audit` (pip-audit) | No known vulnerabilities, 7 ignored |
| Secrets | `gitleaks detect` | No leaks found |
| Migrations, fresh volume | `down -v` then `up -d` | 0001–0003 applied, 3 columns present |
| Migrations, populated volume | seeded then applied | Applied, rows intact, re-run is a no-op |
| Startup tool validation | typo injected into `TokenomicsAnalyst` | Reported by name at startup |

### End-to-end user journey

Fresh volume, seeded evaluation, hostile content planted in the report body:

| | |
|---|---|
| `/api/reports/{id}/html` | 200 |
| `/api/reports/{id}/markdown` | 200 |
| `/api/reports/html` (index) | 200 |
| unknown id | 404 |
| `/api/projects/not-a-uuid` | 422 |
| `POST /api/tools/get_price` | 403 (deliberately gated) |
| checkpoint with a future target date | 400, nothing written |
| checkpoint with horizon 45 | 400 |

XSS payload (`</script><script>`, `<img onerror>`) parsed out of the served
bytes with `html.parser`: **0 script elements, 0 img elements, 0 event
handlers.** CSP `default-src 'none'` present, `nosniff` present, no external
subresources.

### The six documented-vs-actual divergences

Each was found by checking the runtime rather than trusting a document.

1. pgvector described as inert with 0 embeddings — **62 chunks, all embedded**.
2. The retrospective's corpus assumed readable from Postgres — **`reports` is
   empty and the 18 June cohort has no rows at all**; it survives only in Notion.
3. `tech_infra_analyst`, joint-highest score weight, **absent from the persona
   map** and running on a one-paragraph fallback.
4. Six of eight data-agent personas **instructed agents to read each other**,
   contradicting the independence the data layer exists for.
5. "The Chair can override a veto with documented reasoning" — **it cannot, and
   never could**. Code and prompt agree; the documentation was wrong.
6. The live container runs a **four-month-old image whose `CMD` carries
   `--reload`** and the repo's does not, so "git pull is the entire deploy" is
   true only by accident and the next rebuild ends it.

Plus one correction to the brief's headline example: the Chair adjudicated on
the Report Writer's 73.5, not the weighted 77.2 — a one-band departure, not the
two-band override the ledger implies.

### What was deliberately not done

- **No write to the production calibration ledger.** The backfill is reviewed
  and its dry-run cross-validates three ways; running it is Jacob's call.
- **No deploy, no push.** `main` is ahead of `origin/main` locally.
- **No host firewall changes.** The commands are in `docs/operations.md` §11,
  ordered so `ufw enable` cannot lock him out.
- **No key rotation.** Needs his accounts.
- **No scoring semantics changed.** Weights, thresholds and `exclude_from_scores`
  are untouched pending ADR 0002.
