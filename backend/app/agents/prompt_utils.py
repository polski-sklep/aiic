"""Shared prompt-building helpers for committee agents."""
from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any


def combine_prompt_sections(*sections: str) -> str:
    """Join non-empty prompt sections with a blank line."""
    cleaned = [section.strip() for section in sections if section and section.strip()]
    return "\n\n".join(cleaned)


def load_trusted_accounts_section() -> str:
    """Load the trusted accounts markdown block used by social-source agents."""
    ta_path = Path(__file__).resolve().parents[1] / "memory" / "trusted_accounts.md"
    if not ta_path.exists():
        return ""

    return (
        "TRUSTED TWITTER/X ACCOUNTS (prioritize these sources, apply credibility rules at the bottom):\n"
        + ta_path.read_text(encoding="utf-8")
    )


#: Agents whose *score* must never be rendered into another agent's prompt.
#:
#: ``docs/CONTRACTS.md`` §4.1 — "Technical Analyst never influences conviction" —
#: was enforced only in the scoring arithmetic (``Orchestrator.exclude_from_scores``).
#: That is the wrong half on its own: a score printed into a peer's prompt
#: influences that peer's judgment, and several of the agents that read prior
#: outputs (Portfolio Manager 0.05, Devil's Advocate) carry conviction weight
#: themselves. ``exclude_from_scores`` is a scoring concept; this is the prompt
#: layer's half of the same constraint.
#:
#: Only the score is withheld. Every other field an agent here emits — the
#: Technical Analyst's entry zones, entry quality and TA-specific risks — still
#: reaches its readers, because timing context is the channel that is *meant*
#: to exist. Add names here rather than writing a name check at a call site.
NON_CONVICTION_SCORE_AGENTS: frozenset[str] = frozenset({"technical_analyst"})

#: Output keys that carry a conviction-bearing score. A section that requests any
#: of these is a score section for the purposes of NON_CONVICTION_SCORE_AGENTS.
SCORE_FIELD_KEYS: frozenset[str] = frozenset({"score", "overall_score"})


def format_prior_outputs_section(
    prior_outputs: Mapping[str, Any],
    heading: str,
    fields: Sequence[tuple[str, str, int | None]],
) -> str:
    """Render prior agent outputs into a compact prompt section.

    Scores belonging to :data:`NON_CONVICTION_SCORE_AGENTS` are withheld. If a
    score is the only thing the section asked that agent for, the agent is
    omitted from the section entirely rather than shown as an empty entry.
    """
    if not prior_outputs:
        return ""

    lines: list[str] = [f"{heading}:"]
    for agent_name, output in prior_outputs.items():
        if not isinstance(output, Mapping):
            continue

        visible_fields = _visible_fields(agent_name, fields)
        if not visible_fields:
            # Nothing this agent is permitted to contribute here. Not a failure,
            # so it gets no placeholder either.
            continue

        field_lines: list[str] = []
        for key, label, limit in visible_fields:
            value = output.get(key)
            if not _has_meaningful_value(value):
                continue
            field_lines.append(f"  {label}: {_stringify_prompt_value(value, limit=limit)}")

        if field_lines:
            lines.append(f"[{agent_name}]")
            lines.extend(field_lines)
        elif _failure_note(output) is not None:
            # QA-044: an agent that ran and failed must stay visible. Deleting it
            # shrinks the apparent roster and the reader reasons as though that
            # agent had never been convened. An agent that simply has nothing to
            # say under this heading is still omitted — a bare name under a
            # heading it contributed nothing to is noise, not information.
            lines.append(f"[{agent_name}]")
            lines.append(f"  {_failure_note(output)}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _visible_fields(
    agent_name: str,
    fields: Sequence[tuple[str, str, int | None]],
) -> list[tuple[str, str, int | None]]:
    """Drop score fields for agents barred from influencing conviction."""
    if agent_name not in NON_CONVICTION_SCORE_AGENTS:
        return list(fields)
    return [field for field in fields if field[0] not in SCORE_FIELD_KEYS]


def _failure_note(output: Mapping[str, Any]) -> str | None:
    """A one-line note if this output is a recorded agent failure, else None."""
    error = output.get("error")
    if error is None:
        return None
    text = str(error).strip()
    if not text:
        return None
    return f"Agent failed, no usable output: {text[:200]}"


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    return True


def _stringify_prompt_value(value: Any, *, limit: int | None = None) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [str(item) for item in value if item is not None]
        if limit is not None:
            items = items[:limit]
        return "; ".join(items)
    if isinstance(value, Mapping):
        return json.dumps(value, default=str, ensure_ascii=True, sort_keys=True)
    return str(value)
