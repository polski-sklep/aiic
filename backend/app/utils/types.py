from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypeAliasType, TypedDict


JSONPrimitive: TypeAlias = str | int | float | bool | None

# `JSONValue` is recursive, and the plain-string forward reference it used to
# carry —
#
#     JSONValue: TypeAlias = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
#
# — is not something Pydantic can resolve. Any model with a `JSONValue`-typed
# field built as a "not fully defined" TypeAdapter, which took down BOTH
# `POST /api/tools/{tool_name}` (request validation) and `/openapi.json`
# (schema generation) with a 500 — one cause, two symptoms, and the second
# meant the whole API was undocumentable and no client could generate against
# it (docs/reviews/security-review.md, Observations).
#
# `TypeAliasType` gives the alias a real runtime object with a lazily evaluated
# value, which is what a recursive type needs: Pydantic builds a proper
# recursive core schema and emits `$defs`/`$ref` in the OpenAPI document.
#
# Chosen over the alternatives deliberately:
#   * `model_rebuild()` at each use site fixes one model and leaves the next
#     one to rediscover the same 500 in production.
#   * `dict[str, Any]` on the request model would weaken, for one class, the
#     type that ~26 modules rely on for static checking.
# Only `api/tools.py::ToolExecuteRequest` consumes these aliases through
# Pydantic; every other use is annotation-only. So the blast radius is one
# class and the strict type survives everywhere.
JSONValue = TypeAliasType(
    "JSONValue", "JSONPrimitive | list[JSONValue] | dict[str, JSONValue]"
)
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
    """The committee's two scores and the Chair's decision, compared.

    There are three different "scores" in this system and they are not the same
    number (docs/adr/0002-score-chair-coherence.md §3):

    * ``_calc_score`` — deterministic, weighted over ten agents. This is what
      lands in ``calibration_records.overall_score``.
    * ``sections.22_overall_score`` — an LLM asked for a weighted average and
      given no weights. This is the number that reaches the **Chair**.
    * ``chair.output["score"]`` — invented by the Chair, parsed, then discarded.

    Handoff §3.1 read the Aave row as the Chair overriding a 77.20 it disliked.
    It did not: `agent/retrospective` recovered the adjudication trace and the
    Chair had read the Report Writer's **73.5**, a WATCH-band number, and
    reasoned coherently about it. The weighted 77.20 was computed nine lines
    later and written to the ledger. The two numbers never met.

    That distinction decides the remedy, so this record keeps the two failure
    modes apart rather than collapsing them into one "conflict" flag:

    * ``divergence`` — the two *scores* disagree with each other. A measurement
      problem: two estimators of the same quantity disagree, and only one is
      stored. Aave was this.
    * ``contradiction`` — the score the Chair actually saw disagrees with the
      Chair's own decision. A judgement problem: the adjudicator departed from
      the evidence in front of it.
    * ``apparent_contradiction`` — the weighted score's band against the
      decision. This is what the ledger alone shows, and it is what handoff
      §3.1 saw. Recorded because it is the view any historical analysis of the
      existing eight rows is stuck with, and because the gap between it and
      ``contradiction`` is exactly the divergence.

    Purely an instrument. Per PROJECT_DECISIONS.md D6 nothing here is shown to
    the Chair and nothing here changes ``decision``.
    """

    weighted_score: float | None
    weighted_band: ScoreBand | None
    chair_visible_score: float | None
    chair_visible_band: ScoreBand | None
    chair_visible_source: str
    chair_decision: str
    chair_confidence: str
    comparable: bool
    divergence: bool | None
    score_delta: float | None
    divergence_bands_apart: int | None
    contradiction: bool | None
    contradiction_bands_apart: int | None
    apparent_contradiction: bool | None
    apparent_bands_apart: int | None
    conflict: bool
    detail: str
    thresholds: dict[str, float]
