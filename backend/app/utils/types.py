from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypedDict


JSONPrimitive: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]
JSONArray: TypeAlias = list[JSONValue]

ToolArguments: TypeAlias = JSONObject
ToolResult: TypeAlias = JSONObject
KnowledgeDatabase: TypeAlias = Literal["all", "transcripts", "learnings", "projects"]


class SourceRecord(TypedDict):
    label: str
    url: str
    kind: str
    tool_name: NotRequired[str]
    agent_name: NotRequired[str]
    supports: NotRequired[str]
    retrieved_at: NotRequired[str]
    id: NotRequired[int]


class FootnoteRecord(TypedDict):
    id: int
    label: str
    url: str
    kind: str
    supports: str
