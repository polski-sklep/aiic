"""Agent persona loader.

Reads multi-file agent personas from app/memory/committee/<folder>/.
Each folder can contain: SOUL.md, SKILLS.md, TOOLS.md, INTERFACES.md,
CONSTRAINTS.md, MEMORY.md. Files are concatenated in a fixed order
to build the agent's full persona context.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

COMMITTEE_DIR = Path(__file__).parent / "committee"

# Map Python agent names to Cowork folder names
AGENT_FOLDERS = {
    "tokenomics_analyst": "economics",
    "technical_analyst": "technical-analyst",
    "governance_analyst": "gov-analyst",
    "onchain_analyst": "onchain-analyst",
    "competitive_intel": "competitive-intel",
    "field_intel": "fed-intelligence",
    "legal_regulatory": "legal-analyst",
    "risk_officer": "risk-officer",
    "maturation_scorer": "valuation-scorer",
    "devils_advocate": "devils-advocate",
    "portfolio_manager": "portfolio-manager",
    "report_writer": "report-writer",
    "ray_dalio": "ray-judge",
    "committee_chair": "governance-chief",
}

# Read order: identity first, then capabilities, then constraints
FILE_ORDER = ["SOUL.md", "SKILLS.md", "TOOLS.md", "INTERFACES.md", "CONSTRAINTS.md", "MEMORY.md"]


def load_agent_persona(agent_name: str) -> str:
    folder_name = AGENT_FOLDERS.get(agent_name)
    if not folder_name:
        return ""

    folder_path = COMMITTEE_DIR / folder_name
    if not folder_path.is_dir():
        logger.warning(f"Persona folder not found: {folder_path}")
        return ""

    parts = []
    for filename in FILE_ORDER:
        filepath = folder_path / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)

    combined = "\n\n---\n\n".join(parts)
    logger.debug(f"Loaded persona for {agent_name} from {folder_name}/: {len(combined)} chars, {len(parts)} files")
    return combined


def reload_personas() -> None:
    logger.info("Agent personas reloaded from disk")


def list_personas() -> dict[str, str]:
    result = {}
    for agent_name, folder_name in AGENT_FOLDERS.items():
        folder_path = COMMITTEE_DIR / folder_name
        if folder_path.is_dir():
            files = [f.name for f in folder_path.iterdir() if f.suffix == ".md"]
            result[agent_name] = f"{folder_name}/ ({len(files)} files: {', '.join(sorted(files))})"
        else:
            result[agent_name] = "MISSING"
    return result
