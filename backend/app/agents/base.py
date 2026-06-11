from __future__ import annotations
import os
import json
import logging
import time
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
    _base_tools: list[str] = ["search_notes", "read_note"]

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
        """Parse the agent's raw text output into structured data."""
        # Try to extract JSON from the response
        text = raw_text.strip()

        # Handle markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return cast(JSONObject, parsed)
        except json.JSONDecodeError:
            # Try to find JSON in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start:end])
                    if isinstance(parsed, dict):
                        return cast(JSONObject, parsed)
                except json.JSONDecodeError:
                    pass

            return {
                "summary": text[:500],
                "parse_error": "Could not parse structured JSON from agent output",
                "raw_output": text,
            }

        return {
            "summary": text[:500],
            "parse_error": "Agent output was valid JSON but not an object",
            "raw_output": text,
        }

    def extract_score(self, output: JSONObject) -> float | None:
        """Extract numeric score from parsed output."""
        score = output.get("score")
        if score is not None:
            try:
                return float(score)
            except (ValueError, TypeError):
                pass
        return None
