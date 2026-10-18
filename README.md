# Committee Orchestrator

A self-hosted investment committee for crypto projects. You send it a project; fifteen agents run a fixed pipeline and it writes a scored report. The decision is `BUY`, `PASS`, `WATCH`, or `VETO`.

It is built for one operator. The HTTP API does not authenticate callers. `SECURITY.md` treats the deployment network (and, for the bot, a single Telegram chat id) as the gate. Reports are server-rendered HTML and markdown. There is no separate frontend application (`PROJECT_DECISIONS.md` D7).

## Stack

- Python 3.12 in the container. FastAPI `0.115.6`, uvicorn, Pydantic v2, SQLAlchemy 2 with asyncpg.
- Postgres 16 (`pgvector/pgvector:pg16`) and Redis 7 (`redis:7-alpine`), both from `docker-compose.yml`.
- Anthropic Claude for agent calls (`anthropic` 0.125.0). Model ids live in `backend/app/config.py`: `claude-opus-5` (strong), `claude-sonnet-5` (balanced), `claude-haiku-4-5-20251001` (fast). The fast tier is declared on `ModelTier` and no agent sets it.
- OpenAI for embeddings (`text-embedding-3-small` in `backend/app/knowledge/__init__.py`). `llm/router.py` selects the OpenAI chat provider when `ANTHROPIC_API_KEY` is empty, and the Claude provider when it is set. The choice is made once, when the router is constructed.
- External data: CoinGecko, DeFiLlama, Binance public market data, Brave Search, the X API, and Notion.

Runtime pins are `backend/requirements.txt`. Lint, types, and `pip-audit` are `backend/requirements-dev.txt`, installed only in the Docker `dev` stage. `pyproject.toml` holds the ruff and mypy settings (and a pytest config block). It is not an installable package. The test suite is stdlib `unittest`; pytest is not installed.

## Layout

| Path | What it is |
| --- | --- |
| `backend/app/agents/` | The fifteen agents, the orchestrator, the structural gate, within-run reconciliation |
| `backend/app/api/` | FastAPI routers |
| `backend/app/llm/` | Claude and OpenAI providers, the router, run-cost pricing |
| `backend/app/tools/` | Tool implementations and `registry.py` |
| `backend/app/knowledge/` | Calibration, cross-report consistency, prior-evaluation history, embeddings |
| `backend/app/memory/` | Mandate, thesis, risk policy, trusted accounts, and per-agent persona markdown |
| `backend/app/models/` | SQLAlchemy models |
| `backend/app/tpl.html` | HTML report template |
| `backend/init.sql` | Schema applied only to an empty Postgres volume |
| `backend/migrations/` | Forward-only SQL migrations, applied on startup |
| `backend/scripts/backfill_checkpoints.py` | Reconstruct missed calibration checkpoints |
| `backend/tests/` | The unittest suite |
| `telegram_bot.py` | Optional Telegram interface. Not a Compose service |
| `docs/` | Contracts, ADRs, operations, reviews |

`Makefile` is the entry point for the stack, tests, and lint. Longer operational notes are in `docs/operations.md`.

## The pipeline

`Orchestrator.evaluate` in `backend/app/agents/orchestrator.py`:

1. Resolve the project and attach a `case_context` baseline (CoinGecko identity plus DeFiLlama TVL, fees, and revenue).
2. Structural gate (`agents/guardrails.py`). A rejection is stored as `gate_failed` and no agent runs.
3. Eight data agents, in parallel, on that shared baseline. They do not see each other's output.
4. A reconciliation pass over the data layer (`agents/reconciliation.py`).
5. In order: Maturation Scorer, Risk Officer, Devil's Advocate, Portfolio Manager, Report Writer. Each sees the outputs collected so far.
6. A second reconciliation pass over the whole run, including the draft report.
7. Ray, an independent review of the draft.
8. The weighted score is computed here, before the Chair, and is kept out of the Chair's prompt.
9. The Chair decides. If the Risk Officer vetoed, the orchestrator records `VETO` regardless of the Chair's text.

A previous evaluation of the same project is rendered for the Report Writer only (`knowledge/history.py`). The data agents do not receive it. When a prior exists, the report gains section 25, `25_what_changed`.

On startup the process applies pending SQL migrations and starts the consistency-sweep loop (`knowledge/consistency_schedule.py`). The sweep is due after 10 new reports, or on the 2nd of the month in `Europe/Warsaw` (`knowledge/consistency.py`).

### Data agents

Parallel. All eight use the balanced tier (Sonnet 5).

| Agent | Class | Domain |
| --- | --- | --- |
| Tokenomics | `TokenomicsAnalyst` | Supply, emissions, value accrual |
| Governance | `GovernanceAnalyst` | DAO design, voting, treasury |
| On-chain | `OnChainAnalyst` | Usage, flows, holder concentration |
| Tech / infra | `TechInfraAnalyst` | Architecture and technical credibility |
| Competitive intel | `CompetitiveIntel` | Category position, peers |
| Field intel | `FieldIntel` | Community, news, sentiment |
| Legal / regulatory | `LegalRegulatory` | Classification, jurisdiction, enforcement |
| Technical analyst | `TechnicalAnalyst` | Levels, trend, order book. Entry timing only |

### Synthesis, report, review

| Agent | Class | Tier | Role |
| --- | --- | --- | --- |
| Maturation Scorer | `MaturationScorer` | strong | Growth and delivery against the roadmap |
| Risk Officer | `RiskOfficer` | strong | Downside. The only veto |
| Devil's Advocate | `DevilsAdvocate` | strong | Contrarian pass |
| Portfolio Manager | `PortfolioManager` | strong | Fit and sizing |
| Report Writer | `ReportWriter` | strong | The report |
| Ray | `RayDalio` | strong | Independent review after the draft |
| Chair | `CommitteeChair` | strong | Final decision |

### Personas

`backend/app/memory/agent_personas.py` loads each persona from `backend/app/memory/committee/<folder>/`. The folder name comes from `AGENT_FOLDERS` and mostly does not match the Python agent name (`tokenomics_analyst` reads `economics/`, `committee_chair` reads `governance-chief/`). Files are concatenated in a fixed order: `SOUL.md`, `SKILLS.md`, `TOOLS.md`, `INTERFACES.md`, `CONSTRAINTS.md`, `MEMORY.md`. An agent missing from the map gets its one-line `role_description` and nothing else.

Personas are read from disk on every call. Editing the markdown takes effect on the next evaluation.

## Scoring

Scored agents return a 0–100 domain score. `SCORE_WEIGHTS` in the orchestrator:

| Agent | Weight |
| --- | --- |
| `tokenomics_analyst` | 0.15 |
| `tech_infra_analyst` | 0.15 |
| `risk_officer` | 0.15 |
| `onchain_analyst` | 0.12 |
| `competitive_intel` | 0.10 |
| `maturation_scorer` | 0.10 |
| `governance_analyst` | 0.08 |
| `field_intel` | 0.05 |
| `legal_regulatory` | 0.05 |
| `portfolio_manager` | 0.05 |

`_calc_score` renormalises over the agents that actually returned a score. `report_writer`, `ray_dalio`, `committee_chair`, and `technical_analyst` are excluded. The Technical Analyst's output reaches the Chair only as `technical_entry_context`.

Bands on the weighted score: `>= 75` is `INVEST`, `60–74` is `WATCH`, below `60` is `PASS`. `INVEST` is the band that lines up with a `BUY` decision. The Chair's string is the recorded recommendation. Both numbers are kept: `overall_score` is `_calc_score`, and `score_reconciliation` on the result says whether that band and the decision agree (`docs/adr/0002-score-chair-coherence.md`). The Report Writer is also asked for a score of its own, section 22, which is a separate figure.

A Risk Officer veto is final. The Chair's prompt says it cannot override one, and the orchestrator overwrites the recorded decision to `VETO`. Whether that should stay this way is an open question in `PROJECT_DECISIONS.md` D10.

A run that writes a report is `completed`, including one where some agents failed. How much of the weight table actually scored is `run_health`. A run that produces no report is `report_failed`, `gate_failed`, or `failed`. A Chair that returns no parseable decision is recorded as `CHAIR_FAILED`, which is not a verdict.

## Tools

Thirteen tools self-register in `backend/app/tools/registry.py`. The roster is asserted by `backend/tests/test_tool_registry.py`.

| Tool | Source |
| --- | --- |
| `get_price`, `get_token_info` | CoinGecko |
| `get_tvl`, `get_protocol_fees`, `get_category_peers` | DeFiLlama |
| `get_klines`, `get_orderbook_depth`, `compute_technical_levels` | Binance public API |
| `web_search` | Brave |
| `search_twitter` | X |
| `search_notes`, `read_note` | Notion, live |
| `semantic_search_notes` | pgvector corpus (`knowledge_chunks`) |

`search_notes` and `semantic_search_notes` are both on every agent (`BaseAgent._base_tools`), along with `read_note`. They search different corpora: Notion live, versus whatever has been embedded locally. A measurement of the two is in `docs/reviews/retrieval-evaluation.md` (24 August 2026).

`get_category_peers` returns the peer set and the category total as separate figures and does not compute a market share. The denominator it would have used is a field on the result.

A tool whose key is missing stays registered and reports that it is unavailable. CoinGecko calls retry on HTTP 429, and on the free-tier body that returns 200 with an error code 429. A demo key is sent as `x-cg-demo-api-key` when `COINGECKO_API_KEY` is set.

## Setup

Docker with Compose v2, `make`, and `git`. The container is the runtime the repo tests against. `docs/operations.md` says a host virtualenv is not supported: the pins target 3.12, and the Makefile has no venv target.

```bash
git clone https://github.com/polski-sklep/aiic.git
cd aiic
cp .env.example .env
```

Fill in `ANTHROPIC_API_KEY` and `POSTGRES_PASSWORD`. `docker-compose.yml` refuses to interpolate without `POSTGRES_PASSWORD`. Then:

```bash
make up
```

That builds the backend image and starts Postgres, Redis, and the API. The API is published on port 8100 unless `BACKEND_HOST_PORT` is set. Postgres and Redis publish on `127.0.0.1` only.

```bash
curl -s http://localhost:8100/health
curl -s http://localhost:8100/api/tools
```

Interactive API docs are at `http://localhost:8100/docs`. `make` with no target lists the other entry points (`make logs`, `make shell`, `make down`).

`./backend/app` and `./backend/migrations` are bind-mounted. The image command is `uvicorn app.main:app --host 0.0.0.0 --port 8100` (`backend/Dockerfile`). `make restart` starts that process again, so it imports the mounted Python. `make up` rebuilds the image. A changed `.env` needs `make recreate`: `docker compose restart` keeps the environment from container creation. Persona markdown is read on every call, so those edits show up on the next evaluation.

`POSTGRES_PASSWORD` is written into the data directory the first time the volume is created. Changing it in `.env` later does not rotate the database password. The rotation sequence is in `docs/operations.md`.

## Configuration

Names only. Annotated copy: `.env.example`. Application settings are `backend/app/config.py` (`Settings`, read through `get_settings()`). Compose variables are read by `docker-compose.yml` and are not `Settings` fields. The bot reads `os.environ` itself.

### Required to boot

| Name | Read by | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | `llm/router.py`, `llm/claude.py` | Claude calls. With this set, the router does not use OpenAI for chat |
| `POSTGRES_PASSWORD` | `docker-compose.yml` | Postgres password and the password inside the composed `DATABASE_URL` |

### Capability keys

| Name | Read by | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | `knowledge/__init__.py`, `llm/router.py`, `llm/openai_provider.py` | Embeddings. Also the chat provider when the Anthropic key is empty |
| `NOTION_API_KEY` | `tools/notion.py`, `tools/notion_tools.py`, `api/knowledge.py` | Notion client |
| `NOTION_TRANSCRIPTS_DB` | same | Transcripts database id |
| `NOTION_LEARNINGS_DB` | same | Learnings database id |
| `NOTION_PROJECTS_DB` | same, and the orchestrator's report writeback | Projects database id |
| `BRAVE_SEARCH_API_KEY` | `tools/web_search.py` | `web_search` |
| `COINGECKO_API_KEY` | `tools/coingecko.py` | Demo-tier CoinGecko header. Calls work without it, subject to the unkeyed limit |
| `X_BEARER_TOKEN` | `tools/twitter.py` | `search_twitter` |

### App and model settings

| Name | Read by | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `database.py`, `knowledge/consistency_schedule.py`, `scripts/backfill_checkpoints.py` | Async Postgres URL. Compose sets it; putting it in `.env` does not override the container |
| `REDIS_URL` | declared on `Settings`; Compose sets it | No application code reads it |
| `FRONTEND_URL` | `main.py` | Single CORS origin |
| `BACKEND_URL` | `tools/notion.py` (`resolve_report_base`) | Base URL for report links written to Notion. A loopback value is replaced by the fallback constant in that file |
| `LOG_LEVEL` | `main.py`, `database.py` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `SONNET_MODEL`, `OPUS_MODEL`, `HAIKU_MODEL` | `llm/claude.py` | Override the Claude ids. Defaults are in `config.py`. A date suffix on the Opus 5 / Sonnet 5 ids 404s |
| `OPENAI_STRONG_MODEL`, `OPENAI_FAST_MODEL` | `llm/openai_provider.py` | OpenAI chat ids, used only on the OpenAI provider path |

### Compose port and name bindings

Read only by `docker-compose.yml`. Defaults are in that file and in the comments in `.env.example`.

`CONTAINER_PREFIX`, `POSTGRES_HOST_PORT`, `POSTGRES_BIND_ADDR`, `REDIS_HOST_PORT`, `REDIS_BIND_ADDR`, `BACKEND_HOST_PORT`, `BACKEND_BIND_ADDR`.

### Telegram bot

Read by `telegram_bot.py`, not by `Settings`.

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token. The process exits the startup path if it is empty |
| `TELEGRAM_ALLOWED_CHAT_ID` | Only this chat is answered. Unset, the bot stays up and ignores everyone |
| `COMMITTEE_API_BASE` | FastAPI base URL the bot calls |
| `COMMITTEE_REPORT_BASE` | Base URL used in report links it sends |
| `COMMITTEE_QUEUE_FILE` | JSON file for the job queue. Default is `bot_queue.json` beside the script |
| `COMMITTEE_JOB_MINUTES` | ETA shown in the queue. Not a timeout |
| `COMMITTEE_JOB_COST_USD` | Cost figure shown per queued job. Not a spend cap |
| `COMMITTEE_CONFIRM_TIMEOUT` | How long a confirmation prompt stays open |
| `COMMITTEE_STALL_WARN`, `COMMITTEE_STALL_REPEAT` | Stall warnings while a job runs |
| `COMMITTEE_MAX_QUEUE` | Cap on queued jobs |

### Declared and unused

These are fields on `Settings` so an old `.env` still parses. No application code reads them: `ETHERSCAN_API_KEY`, `DUNE_API_KEY`, `GITHUB_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`. There is no auth layer and no Etherscan, Dune, or GitHub tool. `alembic` is pinned in `requirements.txt` and is not configured; schema changes are the SQL files in `backend/migrations/`.

## Running an evaluation

`POST /api/evaluate` runs the pipeline inside the request and returns when it finishes. `arq` is pinned and unused; nothing in the API hands the job to a worker. The bot's queue is a JSON file, not Redis.

```bash
curl -X POST http://localhost:8100/api/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "Chainlink",
    "ticker": "LINK",
    "coingecko_id": "chainlink",
    "category": "Infrastructure",
    "chain": "Ethereum"
  }'
```

`EvaluateRequest` also accepts `website` and `additional_context`. Fetch a stored run with `GET /api/evaluate/{evaluation_id}`.

Other routers, all included from `backend/app/main.py`:

| Prefix | Router |
| --- | --- |
| `/api/calibration` | Scorecard, records, pending checkpoints, `POST /checkpoint/{id}/{30\|90\|180}` |
| `/api/consistency` | Sweep status, trigger, findings |
| `/api/knowledge` | Notion search, page read, sync into pgvector |
| `/api/memory` | Mandate, thesis, risk policy; `POST /reload` drops the memory cache |
| `/api/projects` | Project rows |
| `/api/reports` | List, HTML (`/html` and `/{id}/html`), markdown |
| `/api/tools` | List and invoke a tool |
| `/health` | Process up, which LLM keys are set, which Notion databases are set |

Checkpoints re-price at 30, 90, and 180 days from `entry_captured_at`, not from the day you run the job. A future date is refused. `?as_of=YYYY-MM-DD` overrides the observation date. `backend/scripts/backfill_checkpoints.py` defaults to a dry run; `--commit` writes, and stamps the row so a backfill is distinguishable from a timely checkpoint.

`init.sql` runs only on a fresh volume. After that, schema changes are `backend/migrations/`, applied during startup and by `make migrate`.

## Tests

```bash
make test
```

Builds the production image and runs `python3 -m unittest discover -s tests -t .` inside it, with `backend/` mounted at `/app`. `make test-v` is the same command, verbose. `make lint` and `make typecheck` use the dev image. CI (`.github/workflows/ci.yml`) runs the compose schema check, the import check, that unittest command, ruff, and mypy. `.github/workflows/security.yml` runs gitleaks and a weekly dependency audit.

`make test-pytest` calls pytest in the dev image. pytest is not in `requirements-dev.txt`, so that target has nothing to run.

## Telegram

`telegram_bot.py` is a separate process. It is not in `docker-compose.yml` and not installed by `backend/requirements.txt`. It imports `telegram` (the `python-telegram-bot` package) and `httpx`. The file's own comment says it runs under the host interpreter, which on the VPS is Python 3.10, and it loads `backend/app/llm/pricing.py` and `backend/app/degradation.py` by path so it does not import the 3.12 backend package.

```bash
python3 telegram_bot.py
```

Commands registered in the script: `/start`, `/queue`, `/cancel`, `/reports`, `/health`. A project message is queued and asked for confirmation before the bot calls `POST /api/evaluate`. Messages from any other chat are dropped.

## Working memory

Four files in `backend/app/memory/`:

- `mandates.md` — what the committee is allowed to do
- `risk_policy.md` — thresholds and kill criteria
- `thesis.md` — the investment thesis shipped with the repo
- `trusted_accounts.md` — sources the social agents are pointed at

`mandates.md`, `risk_policy.md`, and `thesis.md` are cached at first read (`memory/__init__.py`). `POST /api/memory/reload` clears that cache. `trusted_accounts.md` is opened from disk by the agents that use it.

## Deployment

`backend/Dockerfile` is multi-stage. `runtime` is the last stage, runs as uid 10001, and is what `docker-compose.yml` builds (`target: runtime`). The `dev` stage adds the lint and audit tools and is not the production image.

`docs/operations.md` and `docs/CONTRACTS.md` §4.7 describe the deploy as `git pull --ff-only` on the VPS checkout at `/opt/committee-orchestrator`. `PROJECT_DECISIONS.md` D14 records that the image command has no `--reload`, so a pull of Python does not reach a running process until the backend container is rebuilt:

```bash
git pull --ff-only && docker compose up -d --build backend
```

Persona markdown is read from disk on every call, so an updated file is visible on the next evaluation. `docs/operations.md` describes `sync-committee` as a markdown-only rsync onto the VPS; that command is not a target in this repo. `.github/workflows/` contains CI and secret scanning, and the tree has no systemd unit. The consistency sweep runs inside the API process.

## What the repo says is not built

- Etherscan, Dune, GitHub, Snapshot, Tally, Safe, and Token Terminal tools (`docs/CONTRACTS.md` §3.6).
- A transcription pipeline. `WHISPER_MODEL` is not a setting.
- A background worker. `arq` is unused, and so is Redis from the application's point of view.
- Authentication. `python-jose` was removed from `requirements.txt`.
- The thematic scan specified in `docs/specs/thematic-scan.md`.

The calibration scorecard's metric is discrimination between `BUY` and `PASS`. `AIIC_HANDOFF.md` says meaningful discrimination needs dozens of resolved cases per bucket.

## Security

`.env` is gitignored. `.gitleaks.toml` is the secret-scan config; the security workflow runs it on pushes and pull requests. See `SECURITY.md` for how to report a vulnerability.

## Licence

MIT. Copyright (c) 2026 Jacob Kowalewski. See `LICENSE`.
