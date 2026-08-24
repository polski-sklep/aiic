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
| 4 | Backfill with true historical prices and correct provenance | `backend/scripts/backfill_checkpoints.py`, `--dry-run` default | Dry-run output **not yet reviewed** — gates the production write | **PARTIAL** |
| 5 | Run the qualitative retrospective | `docs/retrospective/**` | In progress | **PARTIAL** |
| 6 | Risk Officer persona work, 4 open thresholds | `risk-officer/` rewritten; thresholds from D4 | Persona loads, 9754 chars; three-way list alignment in progress | **PARTIAL** |

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
| Chair decides **before** the weighted score exists (line 222 vs 231) | `agent/core` — compute first, record contradictions | In progress | **PARTIAL** |
| Chair's 24-section report sliced to 6000 chars mid-JSON | `agent/core` | In progress | **PARTIAL** |
| `WATCH` unfalsifiable — Chair emits `signposts`, ledger discards them | `agent/core`, migration 0003 | In progress | **PARTIAL** |
| `/openapi.json` and `POST /api/tools/{name}` 500 on an unrebuilt Pydantic forward-ref | `agent/core` | Reproduced; cause traced to recursive `JSONValue` | **PARTIAL** |
| `/api/projects/{id}` 500s on non-UUID instead of 422 | `agent/core` | Reproduced | **PARTIAL** |
| Stored XSS: `</script>` breaks out of the report page and runs JS | `agent/ui-report`, server-side rendering | Exploit demonstrated in a real browser; fix in progress | **PARTIAL** |
| `marked` from CDN, unpinned, no SRI | `agent/ui-report` | In progress | **PARTIAL** |
| `ufw` inactive, `INPUT` policy `ACCEPT`, all services on `0.0.0.0` | `agent/devops` — bind DB/Redis to loopback; host rules documented for Jacob | Ports confirmed filtered externally today | **PARTIAL** |

## C. Non-functional requirements

| Requirement | Implementation | Status |
|---|---|---|
| Tests exist and run | ~80 tests under `backend/tests/`, plain `unittest` | **PARTIAL** |
| CI | `agent/devops`, `.github/` | **PARTIAL** |
| Lint / type checking | `pyproject.toml`, ruff + mypy scoped to a passing baseline | **PARTIAL** |
| Migrations work on the live volume | Runner + `0001`/`0002` proven on a populated volume | **DONE** |
| Documentation | `README.md`, `docs/operations.md`, `docs/adr/`, `docs/reviews/` | **PARTIAL** |
| `.env.example` accurate | `agent/devops` — remove tools that do not exist | **PARTIAL** |
| Dependabot: 1 critical, 3 high, 2 moderate, 3 low | `agent/devops`, `docs/reviews/dependency-audit.md` | **PARTIAL** |
| Secrets never committed | `.gitleaks.toml` extended to Anthropic keys; `git check-ignore` verified | **PARTIAL** |
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
