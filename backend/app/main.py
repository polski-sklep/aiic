import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.calibration import router as calibration_router
from app.api.consistency import router as consistency_router
from app.api.evaluate import router as evaluate_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.projects import router as projects_router
from app.api.reports import router as reports_router
from app.api.tools import router as tools_router
from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Committee Orchestrator starting up")
    logger.info(f"Claude API configured: {bool(settings.anthropic_api_key)}")
    logger.info(f"OpenAI API configured: {bool(settings.openai_api_key)}")

    # Apply schema migrations before serving.
    #
    # `backend/init.sql` runs only on an empty data directory, so the live
    # volume — weeks of uptime, and the only copy of the calibration ledger —
    # never sees a new column added there. Until now the only way to converge it
    # was the manual `docker compose exec backend python -m app.database`, which
    # is a step a deploy can forget, and forgetting it means the code expects
    # columns the database does not have.
    #
    # Deliberately not fatal. `run_migrations()` never raises and reports
    # failures in its return value — verified here against an unreachable
    # database, a malformed DSN, invalid SQL, duplicate versions and checksum
    # drift, all of which return ok=False rather than propagating. It also takes
    # a Postgres advisory lock, verified to block while another connection holds
    # it, so two backends starting together serialise instead of racing. A
    # migration runner that can stop the service from booting is worse than the
    # manual command it replaces: a schema problem would become an outage, and
    # the API surface that does not touch the database would go down with it.
    from app.database import run_migrations

    result = await run_migrations()
    if not result["ok"]:
        logger.error("Schema migrations reported errors: %s", result["errors"])

    # Pre-initialize tool registry
    from app.tools import get_tool_registry

    registry = get_tool_registry()
    logger.info(f"Tools loaded: {registry.tool_names}")

    # tool_names is static class data, so an unresolvable name is a programming
    # error that can be caught here rather than surfacing as an agent quietly
    # running with one fewer capability for every evaluation from now on
    # (QA-026). Logged, not fatal: one mistyped tool should not take the service
    # down, and every other agent still works.
    from app.tools.registry import validate_agent_tool_names

    for problem in validate_agent_tool_names():
        logger.error("Tool wiring: %s", problem)

    yield

    logger.info("Committee Orchestrator shutting down")


app = FastAPI(
    title="Committee Orchestrator",
    description="AI-powered crypto investment committee",
    version="0.1.0",
    lifespan=lifespan,
)

allowed_origins = [origin for origin in (settings.frontend_url,) if origin]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(evaluate_router)
app.include_router(calibration_router)
app.include_router(tools_router)
app.include_router(projects_router)
app.include_router(knowledge_router)
app.include_router(memory_router)
app.include_router(reports_router)
app.include_router(consistency_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_providers": {
            "claude": bool(settings.anthropic_api_key),
            "openai": bool(settings.openai_api_key),
        },
        "notion": {
            "connected": bool(settings.notion_api_key),
            "transcripts_db": bool(settings.notion_transcripts_db),
            "learnings_db": bool(settings.notion_learnings_db),
            "projects_db": bool(settings.notion_projects_db),
        },
    }
