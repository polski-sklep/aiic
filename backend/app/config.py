from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://committee:committee_dev_pw@localhost:5432/committee"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # External APIs
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    dune_api_key: str = ""
    github_token: str = ""
    brave_search_api_key: str = ""
    x_bearer_token: str = ""

    # Auth
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret: str = ""  # Required: set a strong random secret in .env (e.g. openssl rand -hex 32)

    # Notion
    notion_api_key: str = ""
    notion_transcripts_db: str = ""  # Database ID for IC call transcripts
    notion_learnings_db: str = ""  # Database ID for learnings/notes
    notion_projects_db: str = ""  # Database ID for project evaluations

    # App
    backend_url: str = "http://localhost:8100"
    frontend_url: str = "http://localhost:3100"
    log_level: str = "INFO"

    # LLM Model Defaults
    sonnet_model: str = "claude-sonnet-4-6"
    opus_model: str = "claude-opus-4-8"
    haiku_model: str = "claude-haiku-4-5-20251001"
    openai_strong_model: str = "gpt-4o"
    openai_fast_model: str = "gpt-4o-mini"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
