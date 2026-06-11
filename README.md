# Committee Orchestrator

Multi-agent AI investment committee for crypto project evaluation. Thirteen specialist agents plus an independent "Ray" review run a structured pipeline (knowledge retrieval → parallel data-gathering → sequential synthesis → report) and produce scored reports with risk veto authority.

Backend: FastAPI · Postgres + pgvector · Claude or OpenAI provider. Frontend: Next.js 15 (planned).

## Status

Working:

- FastAPI backend with async Postgres
- LLM router with Claude and OpenAI providers
- `BaseAgent` with tool-calling loop
- TokenomicsAnalyst agent and the orchestrator end-to-end
- Tool registry covering market, DeFi, web, and Notion sources
- Knowledge / embedding foundation (pgvector)
- API endpoints: `/evaluate`, `/tools`, `/projects`, `/health`, `/reports`

Pending: most of the 13 agent prompts beyond Tokenomics; tool coverage expansion; Next.js frontend; transcription pipeline.

## Architecture

```
User → FastAPI → Orchestrator → 13 Core Agents + Ray Review
                                       ↓
                                 Tool Registry → External APIs
                                       ↓
                                 Postgres + pgvector (knowledge, reports)
```

See `docs/committee-orchestrator-blueprint.md` for the full design document.

## Quick start

### 1. Prerequisites

- Docker and Docker Compose
- API keys for at least one LLM provider (Anthropic or OpenAI), plus any external data sources you want enabled (CoinGecko, DeFiLlama, etc.)

### 2. Configure

```bash
git clone <your-fork-url> committee-orchestrator
cd committee-orchestrator
cp .env.example .env
# Edit .env and fill in your keys.
```

Required: one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. All external API keys (CoinGecko, Etherscan, Dune, GitHub, Brave Search, Notion, X) are optional — tools that depend on missing keys are stubbed and the agent that called them will note the gap in its output.

### 3. Run

```bash
docker compose up -d
```

This brings up Postgres 16 with pgvector (port 5432), Redis (port 6379), and the FastAPI backend (port 8100).

### 4. Verify

```bash
curl http://localhost:8100/health

curl http://localhost:8100/api/tools | python3 -m json.tool

curl -X POST http://localhost:8100/api/tools/get_price \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"coin_id": "ethereum"}}'
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

### 6. (Optional) Telegram surface

`telegram_bot.py` wraps the API in a chat interface. Set these env vars before running:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=
COMMITTEE_API_BASE=http://localhost:8100
COMMITTEE_REPORT_BASE=http://localhost:8100
```

Then `python telegram_bot.py`. Only messages from `TELEGRAM_ALLOWED_CHAT_ID` are processed.

## Local development (without Docker)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start only Postgres + Redis via Docker
docker compose up postgres redis -d

# Run backend natively
uvicorn app.main:app --reload --port 8100
```

## Working memory

The committee reads four markdown files in `backend/app/memory/` as part of its working memory:

- `mandates.md` — explicit committee mandates and constraints
- `risk_policy.md` — risk thresholds, kill criteria, downgrade rules
- `thesis.md` — investment thesis (a template ships with the repo; fill in your own views)
- `trusted_accounts.md` — vetted research sources

Edit these to encode your own investment posture, then restart the backend so the new memory loads.

Each agent also has a per-agent memory directory at `backend/app/memory/committee/<agent-slug>/` with files like `SOUL.md`, `SKILLS.md`, `TOOLS.md`, `INTERFACES.md`, and `CONSTRAINTS.md`.

## Tool registry

Tools live under `backend/app/tools/` and self-register into a central registry. Categories:

- **Market and DeFi data** — CoinGecko, DeFiLlama, Token Terminal
- **On-chain** — Etherscan, Dune, GitHub
- **Governance** — Snapshot, Tally, Safe API
- **Research** — Brave Search / Serper, X/Twitter, internal pgvector
- **Notion** — transcripts, learnings, projects

Tools without configured keys are tagged "unavailable" rather than removed; the agent calling them adapts.

## Scoring

Each agent produces a 0–100 domain score. The overall recommendation is weighted:

| Agent          | Weight | Domain                    |
| -------------- | ------ | ------------------------- |
| Tokenomics     | 15%    | Token design quality      |
| On-Chain       | 12%    | Network health, usage     |
| Tech/Infra     | 15%    | Technical soundness       |
| Governance     | 8%     | Decentralisation quality  |
| Competitive    | 10%    | Market positioning        |
| Field Intel    | 5%     | Community / sentiment     |
| Risk           | 15%    | Risk-adjusted (inverted)  |
| Maturation     | 10%    | Growth trajectory         |
| Legal          | 5%     | Compliance readiness      |
| Portfolio Fit  | 5%     | Diversification value     |

Thresholds: ≥75 INVEST · 60–74 WATCH · <60 PASS. The Risk Officer is the only agent with veto authority; a veto overrides any score, and the chair can override the veto with documented reasoning.

## Security

`.env` is in `.gitignore`. The Telegram surface drops every message that is not from `TELEGRAM_ALLOWED_CHAT_ID`. See `SECURITY.md`.

## Licence

MIT. See `LICENSE`.
