# Active agents

Integration branch: `integration`. All agent branches cut from `5d3c033`.
Worktrees live at `/Users/Jacob/Projects/aiic-worktrees/<name>`.

Shared interfaces and file ownership: [`docs/CONTRACTS.md`](docs/CONTRACTS.md).
Assumptions and judgement calls: [`PROJECT_DECISIONS.md`](PROJECT_DECISIONS.md).

| Agent | Branch | Responsibility | Status |
|---|---|---|---|
| Architecture | `agent/architecture` | Tree-4 triage, circular-dependency fix, ADRs | Integrated |
| Calibration | `agent/calibration` | Checkpoint date bug, historical pricing, backfill | Integrated (backfill dry-run pending) |
| Persistence | `agent/persistence` | Report persistence, evaluation↔calibration linkage, migrations | Integrated |
| Personas | `agent/personas` | Missing `tech_infra_analyst` persona, Risk Officer redesign | Integrated |
| Retrospective | `agent/retrospective` | 67-day qualitative performance retrospective | Integrated |
| DevOps | `agent/devops` | CI, local dev environment, dependency vulnerabilities | **In progress** |
| QA | `agent/qa` | Independent test suite, adversarial testing | Integrated |
| Security | `agent/security` | Auth, injection, secrets, XSS review | Integrated |
| UI / Report | `agent/ui-report` | Report rendering surface, XSS fix, offline assets | **In progress** |
| Retrieval | `agent/retrieval` | Keyword vs semantic retrieval evaluation, pgvector wiring | Integrated |
| Hardening | `agent/falsifiability` | QA defect closure, dated risks, upside branch | **In progress** |
| Core | `agent/core` | Score sequencing, chair truncation, signposts ledger, migration autorun | **In progress** |

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
- `agent/retrospective`, `agent/security` — documents only, no code

Review agents report; owning agents fix. QA finding a calibration defect files it
against `agent/calibration`, it does not patch `knowledge/calibration.py` itself.
