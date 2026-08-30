from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias, Literal

from app.utils.types import JSONValue, JSONObject, SourceRecord


class ModelTier(str, Enum):
    FAST = "fast"  # Haiku / GPT-4o-mini
    BALANCED = "balanced"  # Sonnet / GPT-4o-mini
    STRONG = "strong"  # Opus / GPT-4o


MessageRole: TypeAlias = Literal["system", "user", "assistant", "tool_result"]
MessageContent: TypeAlias = str | list[JSONObject]
ToolParameters: TypeAlias = JSONObject
SourceReference: TypeAlias = SourceRecord


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: ToolParameters


@dataclass
class ToolCall:
    name: str
    arguments: JSONObject
    id: str = ""


@dataclass
class LLMMessage:
    role: MessageRole
    content: MessageContent
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    #: Opaque provider-native content blocks for an `assistant` turn being
    #: replayed. See `LLMResponse.content_blocks` — a provider that set it is
    #: the only thing that reads it back, and any other provider ignores it.
    content_blocks: list[JSONObject] = field(default_factory=list)


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    tokens_input: int
    tokens_output: int
    stop_reason: str | None
    raw: JSONObject = field(default_factory=dict)
    #: The assistant turn exactly as the provider returned it, for replay.
    #:
    #: `content` and `tool_calls` are a lossy view: they keep the text and the
    #: tool calls and drop everything else. Since Opus 5 / Sonnet 5 that
    #: "everything else" includes `thinking` blocks, and a thinking model wants
    #: its own reasoning handed back unmodified on the next round of the same
    #: conversation — removing the blocks can produce ordering/signature 400s.
    #: Reconstructing the turn from `content` + `tool_calls` silently removed
    #: them, so the round trip is preserved verbatim here instead.
    #:
    #: Provider-native and deliberately untyped: nothing outside the provider
    #: that produced it may read or edit it, only carry it back.
    content_blocks: list[JSONObject] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


__all__ = [
    "JSONValue",
    "JSONObject",
    "MessageContent",
    "MessageRole",
    "ModelTier",
    "SourceReference",
    "ToolCall",
    "ToolDefinition",
    "ToolParameters",
    "LLMMessage",
    "LLMResponse",
]
