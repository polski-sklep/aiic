from __future__ import annotations
import logging
from app.llm import LLMMessage, LLMResponse, ToolDefinition, ModelTier
from app.llm.claude import ClaudeProvider
from app.llm.openai_provider import OpenAIProvider
from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMRouter:
    """Routes LLM calls to the configured provider."""

    def __init__(self):
        settings = get_settings()
        if settings.anthropic_api_key:
            self._provider = ClaudeProvider()
            self.provider_name = "claude"
        elif settings.openai_api_key:
            self._provider = OpenAIProvider()
            self.provider_name = "openai"
        else:
            raise RuntimeError("No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

        logger.info("LLM router configured for %s", self.provider_name)

    async def complete(
        self,
        messages: list[LLMMessage],
        tier: ModelTier = ModelTier.BALANCED,
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        return await self._provider.complete(
            messages=messages,
            tier=tier,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )


# Singleton
_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
