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


ScoreBand: TypeAlias = Literal["INVEST", "WATCH", "PASS"]


class ScoreReconciliation(TypedDict):
    """Whether the committee's weighted score and the Chair's call agree.

    The two numbers are produced by different methods — ``_calc_score`` is
    deterministic weighted arithmetic over the ten scored agents, the decision
    is an LLM judgement — and until this record existed nothing in the pipeline
    compared them. Aave on 11 June 2026 scored 77.20 (INVEST band) and the Chair
    returned PASS at high confidence; both were written to adjacent columns and
    the disagreement was invisible (docs/CONTRACTS.md §2.6, docs/adr/0002).

    This structure is an *instrument*, not a control: it records the
    disagreement so the conflict rate is measurable, and changes nothing about
    how either value is produced. Per PROJECT_DECISIONS.md D6 the score is not
    shown to the Chair and the decision semantics are untouched.

    ``comparable`` is False whenever the comparison is not meaningful — no
    score was computable, the Risk Officer vetoed (so the decision is not the
    Chair's own read of the evidence), or the decision is not one of
    BUY/PASS/WATCH. ``conflict`` is always False when ``comparable`` is False.
    """

    overall_score: float | None
    score_band: ScoreBand | None
    band_implied_decision: Literal["BUY", "WATCH", "PASS"] | None
    chair_decision: str
    chair_confidence: str
    comparable: bool
    conflict: bool
    detail: str
    thresholds: dict[str, float]
