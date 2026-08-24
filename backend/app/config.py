from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Database
    # No credential in the default. Compose always supplies DATABASE_URL, built
    # from POSTGRES_PASSWORD, so this default is only ever reached by a caller
    # running outside the stack — and a plausible-looking password baked into
    # source is exactly what gets copied into a real deployment (QA-036). An
    # empty value fails fast and visibly at connect time instead.
    database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # External APIs
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    dune_api_key: str = ""
    github_token: str = ""
    brave_search_api_key: str = ""
    x_bearer_token: str = ""

    # Auth — declared but unused. There is no auth layer: no endpoint reads any
    # of these and nothing signs or verifies a token, so an empty jwt_secret
    # cannot weaken anything that exists (QA-036/037). They are kept declared
    # rather than deleted so that an existing .env carrying them keeps parsing,
    # and so the absence of auth stays visible here rather than looking like an
    # oversight. If auth is ever added, jwt_secret must be made required and
    # startup must refuse an empty value — see docs/reviews/security-review.md
    # SEC-03.
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_secret: str = ""

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
