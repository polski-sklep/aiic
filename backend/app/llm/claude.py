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

# --- Thinking and effort -------------------------------------------------
#
# THIS IS A DELIBERATE CHOICE, NOT A DEFAULT. Read before changing either
# constant.
#
# On Opus 4.8 and Sonnet 4.6, omitting `thinking` meant the model did not
# think. On Opus 5 and Sonnet 5, omitting it runs ADAPTIVE THINKING. This file
# omitted it, so the model-id swap in config.py would have turned thinking on
# for all fifteen agents by accident — a capability change and a cost change
# arriving disguised as a version bump. Both parameters are therefore set
# explicitly here, so that neither is ever again decided by a provider default.
#
# WHY THINKING IS ON RATHER THAN DISABLED
#
# Preserving the old behaviour exactly would mean `{"type": "disabled"}`, and
# that is the one option this codebase cannot afford. Disabled thinking on
# Opus 5 has two documented failure modes, and this harness is the worst case
# for both:
#
#   1. The model sometimes writes a tool call into its VISIBLE TEXT instead of
#      emitting a `tool_use` block. The turn succeeds, the call never runs, and
#      nothing raises. `BaseAgent.run` treats "no tool calls" as "this is the
#      final answer" and hands the text straight to `parse_output` — so a
#      request to fetch data would be recorded as the agent's verdict. The
#      failure is documented as most common on tool-heavy search workloads,
#      which is exactly what the eight data agents are.
#   2. `<thinking>` tags can leak into the visible response — into the same
#      text that `parse_output` has to read as JSON.
#
# Neither is detectable from the persisted record afterwards. Adaptive thinking
# removes both, and is the documented recommendation over disabling.
#
# WHY effort="high" RATHER THAN "xhigh"
#
# `high` is the current API default for both models, so pinning it changes
# nothing today — that is the point. It is written down so the next default
# change is a diff rather than a surprise, which is the whole lesson of this
# migration.
#
# `xhigh` is the recommended setting for hard *coding and agentic* work, and it
# is tempting to read this committee as agentic. It is not the shape those
# recommendations describe: each agent runs a bounded loop of retrieval calls
# and returns one JSON verdict. The guidance for Opus 5 is explicit that
# `xhigh`/`max` are for measured wins rather than a starting point, that `low`
# and `medium` are unusually strong on this model, and that `xhigh` wants
# `max_tokens` of 64K upwards — against the 4,096–24,576 the agents ask for
# today. Raising effort blind, on a run costing ~$4, would also confound the
# one measurement this change exists to produce.
#
# The sweep to run next, in order, is DOWN not up: `medium` on the BALANCED
# tier first (Sonnet 5 at `medium` is documented as comparable to Sonnet 4.6 at
# `high`, i.e. no worse than what the committee had), then `medium` on STRONG.
# Both are one-line changes measured against the same baseline.
THINKING_MODE: JSONObject = {"type": "adaptive"}
EFFORT = "high"

# `max_tokens` is a hard cap on THINKING PLUS RESPONSE TEXT, and every agent's
# max_tokens was sized when thinking did not exist: ray_dalio and all eight data
# agents ask for 4,096, which a thinking model can spend before it starts
# answering. The symptom would be `stop_reason == "max_tokens"` and a truncated
# JSON body — recoverable-looking damage that `parse_output` would half-repair.
#
# The agent layer asks for the size of the ANSWER it wants and knows nothing
# about thinking, so the headroom is added here, where thinking is switched on.
# Unused headroom is free: billing is on tokens produced, not tokens allowed.
THINKING_HEADROOM_TOKENS = 8192

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
#   * The minimum cacheable prefix is model-dependent. It is 512 tokens on
#     claude-opus-5, down from 1024 on claude-opus-4-8; claude-sonnet-5 is
#     unchanged at 1024. A shorter prefix silently does not cache; there is no
#     error. The placement below is unaffected — a lower floor can only cache
#     more — but a prefix previously written off as too short to cache may
#     now cache on the STRONG tier.
#   * Caches are keyed per model, so the first runs after a model change pay
#     cache writes across the board and read nothing. That is a one-off, not a
#     regression; compare steady state against steady state.
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

            # An assistant turn being replayed goes back exactly as it came:
            # thinking blocks included, in their original order, unmodified.
            # Rebuilding it from `content` + `tool_calls` below would drop the
            # thinking blocks, and a thinking model can reject a continuation
            # whose reasoning has gone missing between rounds.
            if msg.role == "assistant" and msg.content_blocks:
                api_messages.append(
                    {"role": "assistant", "content": list(msg.content_blocks)}
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

        # A cache write costs 1.25x, so break-even is the second request against
        # the same prefix. A call with no tools and no history cannot loop and
        # cannot be followed up, so a breakpoint on it is a guaranteed 25%
        # surcharge with no read to earn it back. Everything else — every
        # agent round, since agents always carry at least the knowledge tools —
        # gets the breakpoints.
        if tools or len(api_messages) > 1:
            self._apply_cache_control(system_blocks, api_messages)

        # Thinking shares the ceiling with the answer — see
        # THINKING_HEADROOM_TOKENS. `max_tokens` is what the agent wants of the
        # ANSWER; the request asks for that plus room to think.
        request_max_tokens = max_tokens + THINKING_HEADROOM_TOKENS

        kwargs: JSONObject = {
            "model": model,
            "max_tokens": request_max_tokens,
            "messages": api_messages,
            "thinking": dict(THINKING_MODE),
            "output_config": {"effort": EFFORT},
        }
        # Claude Opus 4.7+ rejects non-default sampling parameters. Omitting
        # temperature keeps the shared request path compatible across the
        # current Claude models — and CONTRACTS.md §4.4 makes it a defect to
        # reintroduce. The `temperature` argument in this method's signature is
        # accepted and deliberately unused for that reason.
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
        #
        # The threshold is compared against the size actually requested, which
        # now includes the thinking headroom. Every agent therefore streams,
        # where before only the Chair and the Report Writer did. That is the
        # right direction: adaptive thinking makes a slow turn more likely, not
        # less, and streaming is what removes the timeout wall.
        if request_max_tokens > STREAMING_THRESHOLD_TOKENS:
            async with self.client.messages.stream(**kwargs) as stream:
                response = await stream.get_final_message()
        else:
            response = await self.client.messages.create(**kwargs)

        # A safety classifier can decline a request on Opus 5 / Sonnet 5. That
        # arrives as a normal HTTP 200 with `stop_reason == "refusal"` and an
        # EMPTY content list, not as an exception. Nothing below raises on it —
        # `content_text` stays "" and the agent records unparseable output — so
        # without this line the only trace would be an agent that mysteriously
        # returned nothing. Log it where the cause is still visible.
        if response.stop_reason == "refusal":
            logger.error(
                "claude refused model=%s — agent will record empty output. "
                "stop_details=%r",
                model,
                getattr(response, "stop_details", None),
            )

        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            # `thinking` and `redacted_thinking` blocks land here too. They are
            # deliberately not folded into `content_text`: that string is
            # parsed as the agent's JSON verdict. They survive on
            # `content_blocks` below, which is what the replay uses.
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

        # The assistant turn verbatim, for `_build_messages` to replay. Taken
        # from the SDK's own serialisation so it round-trips, with null-valued
        # keys dropped: the SDK emits every optional field of every block type,
        # and a `{"type": "text", "text": ..., "citations": null}` is not a
        # valid block on the way back in.
        content_blocks: list[JSONObject] = []
        raw_content = raw.get("content")
        if isinstance(raw_content, list):
            for entry in raw_content:
                if isinstance(entry, dict):
                    content_blocks.append(
                        {k: v for k, v in entry.items() if v is not None}
                    )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            raw=raw,
            content_blocks=content_blocks,
        )
