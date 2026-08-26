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

#: Heading that opens the volatile tail of a system prompt.
#:
#: Prompt caching is a strict prefix match, so everything an agent re-sends
#: unchanged has to physically precede everything that varies. Text above this
#: heading is stable for the life of an agent (identity, persona, institutional
#: memory, instructions); text below it changes per evaluation (the date, the
#: project, canonical metrics, retrieved prior knowledge).
#:
#: ``run`` splits the rendered prompt here and hands the provider two system
#: blocks, so a cache breakpoint can sit on the stable half. An agent that
#: overrides ``get_system_prompt`` and does not emit the heading simply gets one
#: block and one breakpoint at the end of it — correct, just less reusable.
SYSTEM_PROMPT_VOLATILE_HEADING = "=== THIS EVALUATION ==="


def split_system_prompt(prompt: str) -> str | list[JSONObject]:
    """Split a rendered system prompt into [stable, volatile] text blocks.

    Returns the prompt unchanged when there is no volatile section to separate,
    so the provider keeps its single-block path.

    The list form is already inside ``LLMMessage``'s declared content type
    (``str | list[JSONObject]``), so this is not a new interface. It does mean
    a provider now has to handle a list here: ``ClaudeProvider`` does, and it
    attaches ``cache_control`` to its own copies rather than to these blocks, so
    no provider syntax leaks back out. ``OpenAIProvider`` forwards the list
    unchanged — Chat Completions accepts a content-part array on a system
    message — but it is not the configured provider and this path has not been
    exercised against it. Its usage block also reports cached tokens under a
    different name, so ``_cache_usage`` reads 0 there; see the report.
    """
    # Anchored on both sides so the heading cannot be matched where it merely
    # appears inside a sentence, or inside a persona file that happens to use
    # the same "=== ... ===" style.
    marker = "\n\n" + SYSTEM_PROMPT_VOLATILE_HEADING + "\n"
    head, sep, tail = prompt.partition(marker)
    if not sep or not head.strip() or not tail.strip():
        return prompt
    return [
        {"type": "text", "text": head},
        {"type": "text", "text": sep + tail},
    ]


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
    #: Prompt-cache accounting, summed over every tool round.
    #:
    #: ``tokens_input`` is the *uncached remainder only* — that is what the API
    #: reports and it is now a fraction of the prompt actually sent. Total
    #: prompt size for this agent is
    #: ``tokens_input + cache_write_tokens + cache_read_tokens``; any cost
    #: estimate must price the three separately (writes ~1.25x input, reads
    #: ~0.1x input) rather than reading ``tokens_input`` alone.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


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

        # Ordering is load-bearing, not cosmetic. Prompt caching matches on an
        # exact byte prefix, so every stable section has to sit above every
        # volatile one: identity, persona, institutional memory and the
        # instruction block first, then the date, the project and the
        # per-evaluation data under SYSTEM_PROMPT_VOLATILE_HEADING. The project
        # name used to sit in the middle, which re-cached everything below it on
        # every scan.
        base = f"""You are the {self.name} on a personal crypto investment committee.

{persona}

{institutional_context}{trusted_accounts_context}

INSTRUCTIONS:
1. FIRST, use search_notes to check if there are prior evaluations, IC call transcripts, or learnings about the project named in the THIS EVALUATION section below, or about related projects. Use read_note to get full content of relevant results.
2. Use your other available tools to gather fresh data relevant to your role.
3. Analyze the data thoroughly, incorporating any prior knowledge found.
4. Apply the institutional memory above (mandate constraints, risk policy, thesis) to your assessment.
5. Provide your assessment as structured JSON with the following fields:
   - "summary": A 2-3 sentence overview of your findings.
   - "key_findings": A list of the most important findings (strings).
   - "risks": A list of identified risks (strings). Where a risk has a date, a size and a direction, state all three: "1B XPL unlocks 28 Jul 2026, ~39.8% of float, bearish". An undated risk cannot be watched or graded. Where a risk genuinely resolves on no date, prefix it "Structural:". Never invent a date or a figure to satisfy this.
   - "opportunities": A list of identified opportunities (strings).
   - "data_quality": a JSON object with keys "verified_claims" (integer), "inferred_claims" (integer), "unknown_gaps" (list of strings for things you could not verify)
   - "score": Your score from 0-100 (integer) where 100 is best.
   - "confidence": Your confidence in this assessment: "low", "medium", or "high".
   - "data_sources": List of tools/sources you used.
   - "prior_context_used": Brief note on any prior knowledge incorporated, or "none".
   - "escalations": Any concerns to escalate to Risk Officer or Chair (list, or empty).
   - "mandate_flags": Any mandate violations or constraints triggered (list of strings, or empty)."""

        # --- everything below this line varies per evaluation ---
        base += f"""

{SYSTEM_PROMPT_VOLATILE_HEADING}

Today is {today}. Use this date for all calculations involving time, age, and duration.

You are evaluating: {project}"""

        case_ctx = context.get("case_context", {})
        canonical = case_ctx.get("canonical_metrics", {})
        if canonical:
            import json as _json
            base += (
                "\n\nCANONICAL METRICS — the committee's baseline for this "
                "evaluation. Use these figures. Where you depart from one, say so "
                "in key_findings with your figure, your source and its as-of date:\n"
                f"{_json.dumps(canonical, default=str, sort_keys=True)}"
            )
            base += f"\nCase timestamp: {case_ctx.get('case_time', 'unknown')}"

        # Contradictions the periodic sweep found between past reports. Rendered
        # whether or not canonical metrics resolved — the two are independent,
        # and a contradiction is worth knowing about even for a project whose
        # baseline could not be fetched.
        contradictions = case_ctx.get("known_contradictions")
        if contradictions:
            base += f"\n\n{contradictions}"

        if knowledge:
            base += f"\n\nRELEVANT PRIOR KNOWLEDGE:\n{knowledge}"

        # The output contract stays last so it is the final thing the model
        # reads, as it was before the reorder.
        base += "\n\nRespond ONLY with valid JSON. No markdown, no commentary outside the JSON."

        return base

    # Tools every agent has access to (knowledge retrieval)
    _base_tools: list[str] = ["search_notes", "read_note", "semantic_search_notes"]

    def get_tools(self) -> list[ToolDefinition]:
        """Get tool definitions this agent can use (agent-specific + knowledge tools).

        The order is deterministic and must stay that way. Tool definitions
        render at position 0 of the cached prefix, ahead of the system prompt,
        so a reordered tool array invalidates every cache breakpoint in the
        request. This was ``list(set(...))``: ``set`` iteration order over
        strings depends on PYTHONHASHSEED, which is randomised per process, so
        each worker built a different tool order and no cache entry written by
        one process could ever be read by another.

        ``dict.fromkeys`` dedupes while preserving first-occurrence order, so
        the agent's own declared ordering is kept rather than alphabetised.
        """
        registry = get_tool_registry()
        all_tool_names = list(dict.fromkeys(self.tool_names + self._base_tools))
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
            LLMMessage(role="system", content=split_system_prompt(system_prompt)),
            LLMMessage(
                role="user",
                content=f"Evaluate {context.get('project_name', 'this project')} using your available tools. "
                        f"Additional context: {json.dumps(context.get('project_info', {}))}"
            ),
        ]

        total_input = 0
        total_output = 0
        total_cache_write = 0
        total_cache_read = 0
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
                cache_write, cache_read = _cache_usage(response)
                total_cache_write += cache_write
                total_cache_read += cache_read
                logger.info(
                    "[%s] round %d tokens: input=%d cache_write=%d cache_read=%d output=%d",
                    self.name, round_num, response.tokens_input,
                    cache_write, cache_read, response.tokens_output,
                )
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
                        cache_write_tokens=total_cache_write,
                        cache_read_tokens=total_cache_read,
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
                cache_write_tokens=total_cache_write,
                cache_read_tokens=total_cache_read,
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
                cache_write_tokens=total_cache_write,
                cache_read_tokens=total_cache_read,
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


def _cache_usage(response: LLMResponse) -> tuple[int, int]:
    """``(cache_write_tokens, cache_read_tokens)`` for one response.

    The counts live in the provider's raw ``usage`` block. A provider with no
    prompt cache omits them, so both default to 0 rather than raising — a
    missing metric must not take an agent down.
    """
    usage = response.raw.get("usage") if isinstance(response.raw, dict) else None
    if not isinstance(usage, dict):
        return 0, 0

    def _count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    return _count("cache_creation_input_tokens"), _count("cache_read_input_tokens")


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
