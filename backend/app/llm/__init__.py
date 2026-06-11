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


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    tokens_input: int
    tokens_output: int
    stop_reason: str | None
    raw: JSONObject = field(default_factory=dict)

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
