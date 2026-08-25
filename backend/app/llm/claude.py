from __future__ import annotations

import logging
from typing import cast

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.llm import LLMMessage, LLMResponse, ModelTier, ToolCall, ToolDefinition
from app.utils.types import JSONObject

logger = logging.getLogger(__name__)

# Above this requested output size the provider streams rather than waiting on a
# single response, so a long generation cannot hit the SDK's 600s timeout.
STREAMING_THRESHOLD_TOKENS = 8192

# --- Prompt caching -------------------------------------------------------
#
# An agent loop re-sends its entire conversation on every tool round, so the
# same bytes are billed once per round. Cache reads are 0.1x the input rate and
# cache writes 1.25x, and what is re-sent is byte-identical, so a breakpoint on
# the growing prefix turns the 12x re-send into roughly 1 write + 11 reads.
#
# Rules the placement below depends on, all verified against the current API:
#   * A request may carry at most four ``cache_control`` breakpoints.
#   * ``cache_control`` attaches to a *content block*, so ``system`` must be a
#     list of text blocks rather than a plain string.
#   * The render order is tools -> system -> messages, so a breakpoint on a
#     system block caches the tool definitions with it.
#   * The minimum cacheable prefix is model-dependent — 1024 tokens for both
#     models this project uses (claude-sonnet-4-6 and claude-opus-4-8). A
#     shorter prefix silently does not cache; there is no error.
#   * Each breakpoint searches back at most 20 content blocks for a prior entry,
#     which is why the message breakpoints roll forward every round instead of
#     being pinned to one position.
CACHE_CONTROL: JSONObject = {"type": "ephemeral"}
MAX_CACHE_BREAKPOINTS = 4
# One breakpoint is spent on the stable head of the system prompt; the rest roll
# along the conversation, marking the most recent user/tool-result turns.
ROLLING_MESSAGE_BREAKPOINTS = MAX_CACHE_BREAKPOINTS - 1


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

    @staticmethod
    def _system_blocks(content: object) -> list[JSONObject]:
        """Normalise one system message into a list of text blocks.

        ``system`` has to be a list of blocks for ``cache_control`` to have
        anywhere to attach. A caller that splits its prompt into stable and
        volatile halves passes a list of ``{"type": "text", ...}`` blocks; a
        caller that passes a plain string gets a single block.
        """
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []

        blocks: list[JSONObject] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    # Copy: the caller's dict must not acquire a cache_control
                    # key, which would leak provider syntax back into the agent
                    # layer and into any other provider.
                    blocks.append({"type": "text", "text": str(block.get("text", ""))})
                else:
                    blocks.append({"type": "text", "text": str(block)})
            return [b for b in blocks if b["text"]]

        text = str(content)
        return [{"type": "text", "text": text}] if text else []

    @staticmethod
    def _apply_cache_control(
        system_blocks: list[JSONObject], api_messages: list[JSONObject]
    ) -> None:
        """Place the request's cache breakpoints, in place.

        Two stability boundaries matter:

        * the end of the stable head of the system prompt — identical for this
          agent across every scan, and it carries the tool definitions with it;
        * the end of each of the most recent user/tool-result turns — identical
          to the previous round's prefix, which is where the re-send multiplier
          lives.

        The message breakpoints roll forward each round. The one written last
        round is still within the 20-block lookback of this round's, so each
        request reads what the previous one wrote.
        """
        budget = MAX_CACHE_BREAKPOINTS

        if system_blocks:
            # The first block is the stable head when the caller split its
            # prompt, and the whole prompt when it did not. Either way the
            # volatile tail below it is covered by the message breakpoints.
            system_blocks[0]["cache_control"] = dict(CACHE_CONTROL)
            budget -= 1

        marked = 0
        for message in reversed(api_messages):
            if marked >= min(ROLLING_MESSAGE_BREAKPOINTS, budget):
                break
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                if not content:
                    continue
                message["content"] = [{"type": "text", "text": content}]
                content = message["content"]
            if not isinstance(content, list) or not content:
                continue
            last = content[-1]
            if not isinstance(last, dict):
                continue
            last["cache_control"] = dict(CACHE_CONTROL)
            marked += 1

    def _build_messages(self, messages: list[LLMMessage]) -> tuple[list[JSONObject], list[JSONObject]]:
        system_blocks: list[JSONObject] = []
        api_messages: list[JSONObject] = []

        for msg in messages:
            if msg.role == "system":
                system_blocks.extend(self._system_blocks(msg.content))
                continue

            if msg.role == "tool_result":
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                            }
                        ],
                    }
                )
                continue

            api_msg: JSONObject = {"role": msg.role}

            if msg.tool_calls:
                content_blocks: list[JSONObject] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                api_msg["content"] = content_blocks
            else:
                api_msg["content"] = msg.content

            api_messages.append(api_msg)

        return system_blocks, api_messages

    async def complete(
        self,
        messages: list[LLMMessage],
        tier: ModelTier = ModelTier.BALANCED,
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        model = self._resolve_model(tier)
        system_blocks, api_messages = self._build_messages(messages)
        self._apply_cache_control(system_blocks, api_messages)

        kwargs: JSONObject = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        # Claude Opus 4.7+ rejects non-default sampling parameters. Omitting
        # temperature keeps the shared request path compatible across the
        # current Claude 4.x models.
        if system_blocks:
            kwargs["system"] = system_blocks
        if tools:
            # Tool definitions render at position 0 of the cached prefix. The
            # caller is responsible for producing them in a deterministic order
            # (see BaseAgent.get_tools); this path preserves whatever order it
            # is given and must not reorder or dedupe.
            kwargs["tools"] = self._convert_tools(tools)

        # Stream once the requested output is large.
        #
        # The SDK applies a flat 600s timeout to a non-streaming create(). The
        # Report Writer now asks for up to 24,576 output tokens; at observed Opus
        # rates a full report is 200-260s and the ceiling is 300-480s, so a slow
        # run would hit that timeout and lose a fifteen-agent evaluation that had
        # already succeeded. Streaming removes the wall entirely because bytes
        # keep arriving.
        #
        # get_final_message() returns the same Message object create() would
        # have, so every caller below is unchanged. Small calls keep the simpler
        # non-streaming path.
        if max_tokens > STREAMING_THRESHOLD_TOKENS:
            async with self.client.messages.stream(**kwargs) as stream:
                response = await stream.get_final_message()
        else:
            response = await self.client.messages.create(**kwargs)

        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=cast(JSONObject, block.input),
                        id=block.id,
                    )
                )

        # Cache accounting. `input_tokens` is only the *uncached remainder*, so
        # a caching change that silently does nothing looks identical to one
        # that works unless these two are read. They are logged here and
        # surfaced on `raw["usage"]` for BaseAgent to accumulate.
        cache_write = getattr(response.usage, "cache_creation_input_tokens", None) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", None) or 0
        logger.info(
            "claude usage model=%s input=%d cache_write=%d cache_read=%d output=%d",
            model,
            response.usage.input_tokens,
            cache_write,
            cache_read,
            response.usage.output_tokens,
        )

        raw = response.model_dump()
        # model_dump() already carries these, but only when the SDK version in
        # use models them. Setting them explicitly makes the contract with
        # BaseAgent._cache_usage independent of that.
        usage = raw.get("usage")
        if isinstance(usage, dict):
            usage["cache_creation_input_tokens"] = cache_write
            usage["cache_read_input_tokens"] = cache_read

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            raw=raw,
        )
