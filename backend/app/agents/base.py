from __future__ import annotations
import os
import json
import logging
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import cast

from app.llm import LLMMessage, LLMResponse, ToolDefinition, ModelTier
from app.llm.router import get_llm_router
from app.tools import get_tool_registry
from app.utils.types import JSONObject, SourceRecord
from app.utils.citations import dedupe_sources, extract_sources_from_tool_result

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15  # Max back-and-forth tool-calling rounds


@dataclass
class AgentResult:
    agent_name: str
    output: JSONObject
    score: float | None = None
    model_used: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: int = 0
    error: str | None = None
    tool_calls_made: list[str] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)


class BaseAgent:
    """Base class for all committee agents.

    Subclasses must define:
        - name: str
        - role_description: str
        - tier: ModelTier
        - tool_names: list[str] (tools this agent can use)
        - system_prompt: str

    Subclasses should override:
        - parse_output(raw_text: str) -> dict
        - extract_score(output: dict) -> float | None
    """

    name: str = "base_agent"
    role_description: str = ""
    tier: ModelTier = ModelTier.BALANCED
    tool_names: list[str] = []
    max_tokens: int = 4096
    temperature: float = 0.0

    def get_system_prompt(self, context: JSONObject) -> str:
        """Build the system prompt. Override for custom behavior."""
        from app.memory import get_agent_context
        from app.memory.agent_personas import load_agent_persona

        from datetime import datetime as _dt, timezone as _tz
        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        project = context.get("project_name", "Unknown Project")
        knowledge = context.get("knowledge_context", "")

        # Load agent persona from markdown files
        persona = load_agent_persona(self.name)
        if not persona:
            persona = f"Role: {self.role_description}"

        # Load institutional memory for this agent
        institutional_context = get_agent_context(self.name)

        # Load trusted accounts for social media agents
        trusted_accounts_context = ""
        if self.name in ("field_intel", "devils_advocate", "ray_dalio"):
            ta_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "trusted_accounts.md")
            if os.path.exists(ta_path):
                try:
                    with open(ta_path, encoding="utf-8") as _taf:
                        trusted_accounts_context = (
                            "\n\nTRUSTED TWITTER/X ACCOUNTS (prioritize these sources, apply credibility rules at the bottom):\n"
                            + _taf.read()
                        )
                except OSError as exc:
                    logger.warning("Could not load trusted accounts from %s: %s", ta_path, exc)

        base = f"""You are the {self.name} on a personal crypto investment committee.

{persona}

Today is {today}. Use this date for all calculations involving time, age, and duration.

You are evaluating: {project}

{institutional_context}{trusted_accounts_context}

INSTRUCTIONS:
1. FIRST, use search_notes to check if there are prior evaluations, IC call transcripts, or learnings about this project or related projects. Use read_note to get full content of relevant results.
2. Use your other available tools to gather fresh data relevant to your role.
3. Analyze the data thoroughly, incorporating any prior knowledge found.
4. Apply the institutional memory above (mandate constraints, risk policy, thesis) to your assessment.
5. Provide your assessment as structured JSON with the following fields:
   - "summary": A 2-3 sentence overview of your findings.
   - "key_findings": A list of the most important findings (strings).
   - "risks": A list of identified risks (strings).
   - "opportunities": A list of identified opportunities (strings).
   - "data_quality": a JSON object with keys "verified_claims" (integer), "inferred_claims" (integer), "unknown_gaps" (list of strings for things you could not verify)
   - "score": Your score from 0-100 (integer) where 100 is best.
   - "confidence": Your confidence in this assessment: "low", "medium", or "high".
   - "data_sources": List of tools/sources you used.
   - "prior_context_used": Brief note on any prior knowledge incorporated, or "none".
   - "escalations": Any concerns to escalate to Risk Officer or Chair (list, or empty).
   - "mandate_flags": Any mandate violations or constraints triggered (list of strings, or empty).

Respond ONLY with valid JSON. No markdown, no commentary outside the JSON."""

        case_ctx = context.get("case_context", {})
        canonical = case_ctx.get("canonical_metrics", {})
        if canonical:
            import json as _json
            base += f"\n\nCANONICAL METRICS (use as baseline, flag discrepancies):\n{_json.dumps(canonical, default=str)}"
            base += f"\nCase timestamp: {case_ctx.get('case_time', 'unknown')}"

        if knowledge:
            base += f"\n\nRELEVANT PRIOR KNOWLEDGE:\n{knowledge}"

        return base

    # Tools every agent has access to (knowledge retrieval)
    _base_tools: list[str] = ["search_notes", "read_note", "semantic_search_notes"]

    def get_tools(self) -> list[ToolDefinition]:
        """Get tool definitions this agent can use (agent-specific + knowledge tools)."""
        registry = get_tool_registry()
        all_tool_names = list(set(self.tool_names + self._base_tools))
        return registry.get_definitions(all_tool_names)

    async def run(self, context: JSONObject) -> AgentResult:
        """Execute this agent's evaluation task."""
        start_time = time.monotonic()
        router = get_llm_router()
        registry = get_tool_registry()

        system_prompt = self.get_system_prompt(context)
        tools = self.get_tools()
        tool_calls_made = []
        collected_sources: list[SourceRecord] = []

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(
                role="user",
                content=f"Evaluate {context.get('project_name', 'this project')} using your available tools. "
                        f"Additional context: {json.dumps(context.get('project_info', {}))}"
            ),
        ]

        total_input = 0
        total_output = 0
        model_used = ""

        try:
            for round_num in range(MAX_TOOL_ROUNDS):
                response: LLMResponse = await router.complete(
                    messages=messages,
                    tier=self.tier,
                    tools=tools if tools else None,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )

                total_input += response.tokens_input
                total_output += response.tokens_output
                model_used = response.model

                # If no tool calls, we have the final response
                if not response.has_tool_calls:
                    output = self.parse_output(response.content)
                    score = self.extract_score(output)

                    return AgentResult(
                        agent_name=self.name,
                        output=output,
                        score=score,
                        model_used=model_used,
                        tokens_input=total_input,
                        tokens_output=total_output,
                        latency_ms=int((time.monotonic() - start_time) * 1000),
                        tool_calls_made=tool_calls_made,
                        sources=dedupe_sources(collected_sources),
                    )

                # Process tool calls
                # Add assistant message with tool calls
                messages.append(LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))

                for tc in response.tool_calls:
                    logger.info(f"[{self.name}] Tool call: {tc.name}({tc.arguments})")
                    tool_calls_made.append(tc.name)
                    result = await registry.execute(tc.name, tc.arguments)
                    collected_sources.extend(
                        extract_sources_from_tool_result(
                            tc.name,
                            tc.arguments,
                            result,
                            agent_name=self.name,
                        )
                    )

                    messages.append(LLMMessage(
                        role="tool_result",
                        content=json.dumps(result, default=str),
                        tool_call_id=tc.id,
                    ))

            # Exhausted rounds
            return AgentResult(
                agent_name=self.name,
                output={"error": "Max tool rounds exceeded"},
                model_used=model_used,
                tokens_input=total_input,
                tokens_output=total_output,
                latency_ms=int((time.monotonic() - start_time) * 1000),
                error="Max tool-calling rounds exceeded",
                tool_calls_made=tool_calls_made,
                sources=dedupe_sources(collected_sources),
            )

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}", exc_info=True)
            return AgentResult(
                agent_name=self.name,
                output={"error": str(e)},
                model_used=model_used,
                tokens_input=total_input,
                tokens_output=total_output,
                latency_ms=int((time.monotonic() - start_time) * 1000),
                error=str(e),
                tool_calls_made=tool_calls_made,
                sources=dedupe_sources(collected_sources),
            )

    def parse_output(self, raw_text: str) -> JSONObject:
        """Parse the agent's raw text output into structured data.

        This is the untrusted-text boundary: everything a model writes becomes
        structured data here, and the failure that matters is throwing away a
        recoverable answer, not crashing.
        """
        if not isinstance(raw_text, str):
            # QA-012: `raw_text.strip()` assumed str, so a provider returning
            # structured content blocks took the whole agent down with an
            # AttributeError instead of degrading to unparseable text.
            return {
                "summary": "",
                "parse_error": "Could not parse structured JSON from agent output",
                "raw_output": "",
            }

        text = _strip_code_fence(raw_text.strip())

        parsed, ok = _loads(text)
        if ok:
            if isinstance(parsed, dict):
                return cast(JSONObject, parsed)
            return {
                "summary": text[:500],
                "parse_error": "Agent output was valid JSON but not an object",
                "raw_output": text,
            }

        for candidate in _balanced_object_candidates(text):
            recovered, recovered_ok = _loads(candidate)
            if recovered_ok and isinstance(recovered, dict):
                return cast(JSONObject, recovered)

        return {
            "summary": text[:500],
            "parse_error": "Could not parse structured JSON from agent output",
            "raw_output": text,
        }

    def extract_score(self, output: JSONObject) -> float | None:
        """Extract a 0-100 numeric score from parsed output, or None.

        QA-013: this was ``float(score)`` with no validation, and each value
        rejected below is one the committee used to act on silently.

        * ``NaN`` — json.loads accepts the bare literal and it propagates
          through ``_calc_score``. Every threshold comparison against NaN is
          False, so the committee lands on the bottom band: an unexplainable
          rejection that looks like a considered verdict.
        * ``1e400`` / ``"infinity"`` — both become inf and dominate the average.
        * ``True`` / ``False`` — ``float(True)`` is 1.0, so a plausible
          mis-generation became a maximally bearish vote.
        * out of range — the prompt says 0-100 and nothing enforced it. A model
          answering on a 0-1 scale, in basis points, or with a negative penalty
          reweights the whole committee; 8500 against a 0.15 weight moves the
          overall score by more than 1200 points.

        Returning None puts the agent in the same bucket as one that produced no
        score, and ``_calc_score`` already renormalises those out — so a bad
        score costs its agent a vote rather than corrupting everyone else's.
        """
        score = output.get("score")
        if score is None or isinstance(score, bool):
            return None

        try:
            value = float(score)
        except (ValueError, TypeError):
            return None

        if not math.isfinite(value):
            logger.warning("[%s] Discarding non-finite score %r", self.name, score)
            return None
        if not 0.0 <= value <= 100.0:
            logger.warning("[%s] Discarding out-of-range score %r (expected 0-100)", self.name, score)
            return None
        return value


def _strip_code_fence(text: str) -> str:
    """Remove a markdown fence, closed or not.

    QA-010: this was ``lines[1:-1]``, which assumes a closing fence exists. When
    a model is cut off by max_tokens the closing fence is missing, so the slice
    ate the last line of real JSON — the closing brace — and the recovery path
    then found no ``}`` at all. A fully recoverable payload was discarded.
    """
    if not text.startswith("```"):
        return text

    lines = text.split("\n")[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines).strip()


def _loads(text: str) -> tuple[object, bool]:
    """``(value, parsed_ok)``. Never raises.

    QA-012: only ``json.JSONDecodeError`` was caught. ``json.loads`` raises
    ``RecursionError`` on deeply nested input, which escaped parse_output and
    was caught by ``run``'s blanket handler — recording the agent as *errored*
    rather than as having produced unparseable text, a different and more
    alarming signal for the orchestrator.
    """
    try:
        return json.loads(text), True
    except (ValueError, TypeError, RecursionError):
        return None, False


#: How much text the brace scanner will walk. Recovery is a courtesy for a model
#: that wrapped its JSON in prose, not a parser for arbitrary documents.
_MAX_RECOVERY_SCAN = 1_000_000


def _balanced_object_candidates(text: str, limit: int = 5) -> Iterator[str]:
    """Yield balanced ``{...}`` substrings, first one first.

    QA-011: recovery used to span ``find("{")`` to ``rfind("}")`` over the whole
    text, so one brace anywhere outside the object widened the slice into
    something that is not JSON. All three of these lost a complete, valid
    assessment::

        I will respond in the shape {field: value}: {"score": 85}
        {"score": 85}\\nNote: I used {search_notes} for prior context.
        {"score": 0, "summary": "example"}\\n{"score": 85, "summary": "real"}

    Braces inside string literals are tracked, so a value like
    ``"we use {curly} braces"`` cannot unbalance the scan.
    """
    window = text[:_MAX_RECOVERY_SCAN]
    yielded = 0
    index = 0

    while yielded < limit:
        start = window.find("{", index)
        if start < 0:
            return

        depth = 0
        in_string = False
        escaped = False
        closed_at = -1
        for position in range(start, len(window)):
            char = window[position]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    closed_at = position
                    break

        if closed_at < 0:
            # Unbalanced from here to the end of the window; nothing that starts
            # later can close either, since it would have to close inside this.
            return

        yield window[start : closed_at + 1]
        yielded += 1
        index = closed_at + 1
