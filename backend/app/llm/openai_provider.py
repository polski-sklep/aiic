from __future__ import annotations
import json
import logging
from typing import cast
from openai import AsyncOpenAI
from app.llm import LLMMessage, LLMResponse, ToolCall, ToolDefinition, ModelTier
from app.config import get_settings
from app.utils.types import JSONObject

logger = logging.getLogger(__name__)


class OpenAIProvider:
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _resolve_model(self, tier: ModelTier) -> str:
        settings = get_settings()
        mapping = {
            ModelTier.FAST: settings.openai_fast_model,
            ModelTier.BALANCED: settings.openai_fast_model,
            ModelTier.STRONG: settings.openai_strong_model,
        }
        return mapping[tier]

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[JSONObject]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _build_messages(self, messages: list[LLMMessage]) -> list[JSONObject]:
        api_messages: list[JSONObject] = []

        for msg in messages:
            if msg.role == "tool_result":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                })
                continue

            api_msg: JSONObject = {"role": msg.role, "content": msg.content}

            if msg.tool_calls:
                api_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            api_messages.append(api_msg)

        return api_messages

    async def complete(
        self,
        messages: list[LLMMessage],
        tier: ModelTier = ModelTier.BALANCED,
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        model = self._resolve_model(tier)
        api_messages = self._build_messages(messages)

        kwargs: JSONObject = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        content_text = choice.message.content or ""
        tool_calls: list[ToolCall] = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    name=tc.function.name,
                    arguments=cast(JSONObject, json.loads(tc.function.arguments)),
                    id=tc.id,
                ))

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            tokens_input=response.usage.prompt_tokens,
            tokens_output=response.usage.completion_tokens,
            stop_reason=choice.finish_reason,
            raw=response.model_dump(),
        )
