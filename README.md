# Committee Orchestrator

Multi-agent AI investment committee for crypto project evaluation. Fifteen agents run a structured pipeline (knowledge retrieval -> parallel data gathering -> sequential adversarial synthesis -> report -> independent review -> chair decision) and produce a scored report with `BUY` / `PASS` / `WATCH` / `VETO`.

Backend: FastAPI · Postgres + pgvector · Redis · Anthropic Claude (OpenAI provider available as an alternative). Frontend: Next.js 15 (planned).

## Architecture

```
User -> FastAPI -> Orchestrator
                     |
        8 data agents (parallel, independent)
                     |
        4 synthesis agents (sequential, adversarial)
                     |
        Report Writer -> Ray (independent review) -> Chair
                     |
        Tool Registry -> external APIs
        Postgres + pgvector (knowledge, reports, calibration)
```

Data agents run in parallel and do not see each other's output. Synthesis agents run sequentially, each seeing what came before. The Chair sees the full record including the disagreement between synthesis and challenge.

See `docs/committee-orchestrator-blueprint.md` for the full design document.

## The committee

**Data layer** - eight agents, parallel, independent:

| Agent | Class | Domain |
| --- | --- | --- |
| Tokenomics | `TokenomicsAnalyst` | Token design, supply, emissions |
| Governance | `GovernanceAnalyst` | Decentralisation quality, voting |
| On-Chain | `OnChainAnalyst` | Network health, usage, flows |
| Tech/Infra | `TechInfraAnalyst` | Technical soundness |
| Competitive Intel | `CompetitiveIntel` | Market positioning |
| Field Intel | `FieldIntel` | Community, sentiment |
| Legal/Regulatory | `LegalRegulatory` | Compliance exposure |
| Technical Analyst | `TechnicalAnalyst` | Entry zones, S/R levels, orderbook |

**Synthesis layer** - four agents, sequential:

| Agent | Class | Role |
| --- | --- | --- |
| Maturation Scorer | `MaturationScorer` | Growth trajectory |
| Devil's Advocate | `DevilsAdvocate` | Contrarian challenge |
| Risk Officer | `RiskOfficer` | Downside, fragility - **veto authority** |
| Portfolio Manager | `PortfolioManager` | Diversification fit, sizing |

**Structural** - three agents:

| Agent | Class | Role |
| --- | --- | --- |
| Report Writer | `ReportWriter` | 24-section report |
| Ray | `RayDalio` | Independent macro/cycle contrarian pass, post-report |
| Chair | `CommitteeChair` | Final decision |

Each agent has a persona directory at `backend/app/memory/committee/<agent-slug>/` containing `SOUL.md`, `CONSTRAINTS.md`, `INTERFACES.md`, and where applicable `SKILLS.md`, `TOOLS.md`, `MEMORY.md`. These are loaded at runtime by `memory/agent_personas.py` via an explicit filename map, so files not in the map are ignored.

## Scoring

Scored agents produce a 0-100 domain score. The overall score is a weighted average (`orchestrator._calc_score`):

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

Weights are renormalised over whichever agents returned a score, so a failed agent does not drag the average down.

**Excluded from scoring** (`exclude_from_scores`): `report_writer`, `ray_dalio`, `committee_chair`, `technical_analyst`.

The Technical Analyst exclusion is deliberate. Its output is passed to the Chair as `technical_entry_context` and informs entry timing and signposts, but it must not influence conviction - a good chart is not a reason to own something.

Thresholds: >=75 `INVEST` · 60-74 `WATCH` · <60 `PASS`. The Risk Officer is the only agent with veto authority; a veto overrides the score, and the Chair can override a veto with documented reasoning.

## Status

Working:

- FastAPI backend, async Postgres, Redis
- LLM router (Claude primary; OpenAI provider available). Model tiers: Opus 4.8 (STRONG), Sonnet 4.6 (BALANCED), Haiku 4.5 (FAST)
- `BaseAgent` with tool-calling loop; all fifteen agents implemented with persona files
- Tool registry, eleven tools registered
- Calibration loop: every recommendation captured with entry price and BTC/ETH benchmarks; manual re-pricing checkpoints
- API: `/api/evaluate`, `/api/calibration`, `/api/tools`, `/api/projects`, `/api/knowledge`, `/api/memory`, `/api/reports`, `/health`

Built but inactive:

- **pgvector semantic search.** Schema, embedding pipeline (`text-embedding-3-small`, 1536-dim), ivfflat cosine indexes, `semantic_search`, and a Notion->pgvector sync all exist. No embeddings have been generated (`knowledge_chunks` is empty) and no agent calls `semantic_search` - agents reach prior context through `search_notes`, which is Notion keyword search. Activating this means running the sync endpoint and adding a `semantic_search` agent tool.

Pending: Next.js frontend; transcription pipeline; Etherscan and Dune tools.

## Tool registry

Tools live under `backend/app/tools/` and self-register into `tools/registry.py`. Eleven are currently registered:

- **Market data** (CoinGecko) - `get_price`, `get_token_info`
- **DeFi** (DeFiLlama) - `get_tvl`, `get_protocol_fees`
- **Technical** (Binance public API) - `get_klines`, `get_orderbook_depth`, `compute_technical_levels`
- **Research** - `web_search` (Brave), `search_twitter`
- **Notion** - `search_notes`, `read_note`

Tools without configured keys are tagged unavailable rather than removed; the calling agent notes the gap in its output. CoinGecko calls retry with backoff on 429 and will use `COINGECKO_API_KEY` if set (demo tier, 30 calls/min).

## Quick start

### 1. Prerequisites

- Docker and Docker Compose
- `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`), plus any external data keys you want enabled

### 2. Configure

```bash
git clone https://github.com/polski-sklep/aiic.git committee-orchestrator
cd committee-orchestrator
cp .env.example .env
# Edit .env and fill in your keys.
```

`POSTGRES_PASSWORD` is required - `docker-compose.yml` fails fast if it is unset. All external API keys (CoinGecko, Brave Search, Notion, X) are optional.

### 3. Run

```bash
docker compose up -d
```

Brings up Postgres 16 with pgvector (5432), Redis (6379), and the FastAPI backend (8100). The backend bind-mounts `./backend/app` and reloads on change.

### 4. Verify

```bash
curl http://localhost:8100/health
curl http://localhost:8100/api/tools | python3 -m json.tool
```

### 5. Run an evaluation

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

A full fifteen-agent evaluation takes roughly 8-12 minutes.

### 6. (Optional) Telegram surface

`telegram_bot.py` wraps the API in a chat interface:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=
COMMITTEE_API_BASE=http://localhost:8100
COMMITTEE_REPORT_BASE=http://localhost:8100
```

Then `python telegram_bot.py`. Only messages from `TELEGRAM_ALLOWED_CHAT_ID` are processed.

## Calibration

Every recommendation is written to `calibration_records` with the entry price and BTC/ETH benchmarks at decision time. Checkpoints re-price at 30/90/180 days and compute return and alpha versus BTC.

- `GET /api/calibration/scorecard` - key metric is **discrimination**: did `BUY`s outperform `PASS`es
- `GET /api/calibration/records`, `GET /api/calibration/pending`
- `POST /api/calibration/checkpoint/{id}/{30|90|180}`

Note: `calibration_records` is created by `backend/init.sql`, which only runs on a **fresh** Postgres volume. On an existing database, create the table manually before the first evaluation or the capture will fail.

At small sample sizes this is a logbook, not a skill measurement. Meaningful discrimination needs dozens of resolved cases per bucket.

## Working memory

Four markdown files in `backend/app/memory/` are loaded as committee-wide working memory:

- `mandates.md` - committee mandates and constraints
- `risk_policy.md` - risk thresholds, kill criteria, downgrade rules
- `thesis.md` - investment thesis (template ships with the repo)
- `trusted_accounts.md` - vetted research sources

Edit these to encode your own posture. The backend reloads on file change.

## Local development (without Docker)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

docker compose up postgres redis -d
uvicorn app.main:app --reload --port 8100
```

## Security

`.env` is gitignored. `.gitleaks.toml` allowlists a false positive in `.env.example`. The Telegram surface drops every message not from `TELEGRAM_ALLOWED_CHAT_ID`. See `SECURITY.md`.

## Licence

MIT. See `LICENSE`.
