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


def format_prior_outputs_section(
    prior_outputs: Mapping[str, Any],
    heading: str,
    fields: Sequence[tuple[str, str, int | None]],
) -> str:
    """Render prior agent outputs into a compact prompt section."""
    if not prior_outputs:
        return ""

    lines: list[str] = [f"{heading}:"]
    for agent_name, output in prior_outputs.items():
        if not isinstance(output, Mapping):
            continue

        field_lines: list[str] = []
        for key, label, limit in fields:
            value = output.get(key)
            if not _has_meaningful_value(value):
                continue
            field_lines.append(f"  {label}: {_stringify_prompt_value(value, limit=limit)}")

        if field_lines:
            lines.append(f"[{agent_name}]")
            lines.extend(field_lines)

    return "\n".join(lines) if len(lines) > 1 else ""


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
