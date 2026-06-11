from __future__ import annotations
import logging
from typing import cast
from anthropic import AsyncAnthropic
from app.llm import LLMMessage, LLMResponse, ToolCall, ToolDefinition, ModelTier
from app.config import get_settings
from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

TIER_TO_MODEL = {
    ModelTier.FAST: "claude-haiku-4-5-20251001",
    ModelTier.BALANCED: "claude-sonnet-4-20250514",
    ModelTier.STRONG: "claude-opus-4-20250514",
}


class ClaudeProvider:
    def __init__(self):
        settings = get_settings()
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _resolve_model(self, tier: ModelTier) -> str:
        settings = get_settings()
        mapping = {
            ModelTier.FAST: settings.haiku_model,
            ModelTier.BALANCED: settings.sonnet_model,
            ModelTier.STRONG: settings.opus_model,
        }
        return mapping[tier]

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[JSONObject]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    def _build_messages(self, messages: list[LLMMessage]) -> tuple[str, list[JSONObject]]:
        system = ""
        api_messages: list[JSONObject] = []

        for msg in messages:
            if msg.role == "system":
                system = msg.content if isinstance(msg.content, str) else str(msg.content)
                continue

            if msg.role == "tool_result":
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                        }
                    ],
                })
                continue

            api_msg: JSONObject = {"role": msg.role}

            if msg.tool_calls:
                content_blocks: list[JSONObject] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_msg["content"] = content_blocks
            else:
                api_msg["content"] = msg.content

            api_messages.append(api_msg)

        return system, api_messages

    async def complete(
        self,
        messages: list[LLMMessage],
        tier: ModelTier = ModelTier.BALANCED,
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        model = self._resolve_model(tier)
        system, api_messages = self._build_messages(messages)

        kwargs: JSONObject = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await self.client.messages.create(**kwargs)

        # Parse response
        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    name=block.name,
                    arguments=cast(JSONObject, block.input),
                    id=block.id,
                ))

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            raw=response.model_dump(),
        )
