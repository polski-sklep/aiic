# Active agents

Integration branch: `integration`. All agent branches cut from `5d3c033`.
Worktrees live at `aiic/worktrees/<name>` — inside the repo, one folder for
everything. `/worktrees/` is gitignored; see `docs/CONTRACTS.md` for why that
matters.

Shared interfaces and file ownership: [`docs/CONTRACTS.md`](docs/CONTRACTS.md).
Assumptions and judgement calls: [`PROJECT_DECISIONS.md`](PROJECT_DECISIONS.md).

| Agent | Branch | Responsibility | Status |
|---|---|---|---|
| Architecture | `agent/architecture` | Tree-4 triage, circular-dependency fix, ADRs | Integrated |
| Calibration | `agent/calibration` | Checkpoint date bug, historical pricing, backfill | Integrated |
| Persistence | `agent/persistence` | Report persistence, evaluation↔calibration linkage, migrations | Integrated |
| Personas | `agent/personas` | Missing `tech_infra_analyst` persona, Risk Officer redesign | Integrated |
| Retrospective | `agent/retrospective` | 67-day qualitative performance retrospective | Integrated |
| DevOps | `agent/devops` | CI, local dev environment, dependency vulnerabilities | Integrated |
| QA | `agent/qa` | Independent test suite, adversarial testing | Integrated |
| Security | `agent/security` | Auth, injection, secrets, XSS review | Integrated |
| UI / Report | `agent/ui-report` | Report rendering surface, XSS fix, offline assets | Integrated |
| Retrieval | `agent/retrieval` | Keyword vs semantic retrieval evaluation, pgvector wiring | Integrated |
| Hardening | `agent/falsifiability` | QA defect closure, dated risks, upside branch | Integrated |
| Core | `agent/core` | Score sequencing, chair truncation, signposts ledger, migration autorun | Integrated |
| Report depth | `agent/report-depth` | Per-section briefs, agent-dump budget, chair report budget | Integrated |
| Notion format | `agent/notion-format` | Notion block rendering, 1,000-block ceiling, heading structure | Integrated |
| Report delivery | `agent/report-delivery` | Report files on disk plus links from the Notion page | Integrated |
| Bot queue | `agent/bot-queue` | Confirmation gate, explicit queue with JSON persistence, no timeout | Integrated |
| Notion order | `agent/notion-newest-first` | Newest-first ordering without a move endpoint | Integrated |
| Prompt caching | `agent/prompt-caching` | Stable prefix, cache breakpoints, deterministic tool arrays | Integrated |
| Canonical facts | `agent/canonical-facts` | DeFiLlama/CoinGecko resolution, `case_context` baseline figures | Integrated |
| Delta report | `agent/delta-report` | Section 25 — what changed since the last evaluation | Integrated |
| Consistency audit | `agent/consistency-audit` | Cross-report contradiction sweep, findings ledger, committee warnings | Integrated |
| Intra-run reconcile | `agent/intra-run-reconcile` | Within-evaluation contradiction detection (`reconcile_data` is inert) | **In progress** |

## Ownership

See §1 of `docs/CONTRACTS.md` for the authoritative path→branch table. In short:

- `agent/calibration` — `knowledge/calibration.py`, `api/calibration.py`, `scripts/`
- `agent/persistence` — `models/`, `api/evaluate.py`, `agents/orchestrator.py`, `init.sql`, `migrations/`
- `agent/personas` — `memory/**`
- `agent/devops` — `.github/`, `Dockerfile`, compose, requirements, tooling config
- `agent/ui-report` — `tpl.html`, `api/reports.py`
- `agent/qa` — `tests/**` except `test_calibration.py`
- `agent/architecture` — `tools/contracts.py`, `tools/registry.py`, `docs/adr/`
- `agent/retrieval` — `knowledge/__init__.py`, `api/knowledge.py`, `tools/semantic.py`
- `agent/consistency-audit` — `knowledge/consistency.py`, `api/consistency.py`
- `agent/canonical-facts` — `tools/defillama.py`, `tools/coingecko.py`
- `agent/delta-report` — `knowledge/history.py`
- `agent/intra-run-reconcile` — `agents/reconciliation.py`, the orchestrator's
  reconciliation call site only
- `agent/report-depth` — `agents/report_writer.py`, `agents/chair.py`
- `agent/bot-queue`, `agent/notion-*`, `agent/report-delivery` — `telegram_bot.py`,
  `tools/notion.py`
- `agent/retrospective`, `agent/security` — documents only, no code

Review agents report; owning agents fix. QA finding a calibration defect files it
against `agent/calibration`, it does not patch `knowledge/calibration.py` itself.
