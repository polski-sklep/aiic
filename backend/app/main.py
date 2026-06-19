import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.calibration import router as calibration_router
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

    # Pre-initialize tool registry
    from app.tools import get_tool_registry

    registry = get_tool_registry()
    logger.info(f"Tools loaded: {registry.tool_names}")

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
