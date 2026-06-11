"""Institutional memory loader.

Reads mandates.md, risk_policy.md, and thesis.md from disk and provides
them as context to agents. These files are the "fund constitution" —
they define what the committee is allowed to do, how it evaluates risk,
and what the current investment thesis is.

Files live in app/memory/ and are loaded once at startup, then cached.
Edit the files directly to update investment policy.
"""
from __future__ import annotations
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(__file__).parent


@lru_cache(maxsize=1)
def load_mandates() -> str:
    """Load investment mandate constraints."""
    return _load_file("mandates.md")


@lru_cache(maxsize=1)
def load_risk_policy() -> str:
    """Load risk policy and guardrails."""
    return _load_file("risk_policy.md")


@lru_cache(maxsize=1)
def load_thesis() -> str:
    """Load current investment thesis."""
    return _load_file("thesis.md")


def load_all() -> dict[str, str]:
    """Load all institutional memory files."""
    return {
        "mandates": load_mandates(),
        "risk_policy": load_risk_policy(),
        "thesis": load_thesis(),
    }


def get_agent_context(agent_name: str) -> str:
    """Get the institutional memory context relevant to a specific agent.

    Different agents get different slices of institutional memory:
    - All agents: thesis (so they know what we're looking for)
    - Risk Officer: full risk_policy + mandates
    - Portfolio Manager: mandates + thesis
    - Devil's Advocate / Charlie: thesis (to challenge it)
    - Data agents: thesis sector convictions (to prioritise analysis)
    - Report Writer: everything (to compile coherent report)
    - Committee Chair: everything (final decision maker)
    """
    memory = load_all()

    # Agents that get everything
    if agent_name in ("committee_chair", "report_writer"):
        return (
            "=== INVESTMENT MANDATE ===\n"
            f"{memory['mandates']}\n\n"
            "=== RISK POLICY ===\n"
            f"{memory['risk_policy']}\n\n"
            "=== INVESTMENT THESIS ===\n"
            f"{memory['thesis']}"
        )

    # Risk Officer gets risk policy + mandates
    if agent_name == "risk_officer":
        return (
            "=== RISK POLICY ===\n"
            f"{memory['risk_policy']}\n\n"
            "=== INVESTMENT MANDATE (CONSTRAINTS) ===\n"
            f"{memory['mandates']}"
        )

    # Portfolio Manager gets mandates + thesis
    if agent_name == "portfolio_manager":
        return (
            "=== INVESTMENT MANDATE ===\n"
            f"{memory['mandates']}\n\n"
            "=== INVESTMENT THESIS ===\n"
            f"{memory['thesis']}"
        )

    # Contrarian agents get thesis (to challenge it)
    if agent_name in ("devils_advocate", "ray_dalio"):
        return (
            "=== INVESTMENT THESIS (YOUR JOB IS TO CHALLENGE THIS) ===\n"
            f"{memory['thesis']}"
        )

    # Legal gets mandates (exclusions, constraints)
    if agent_name == "legal_regulatory":
        return (
            "=== INVESTMENT MANDATE (EXCLUSIONS & CONSTRAINTS) ===\n"
            f"{memory['mandates']}"
        )

    # Data-gathering agents get thesis sector convictions only (trimmed)
    thesis = memory["thesis"]
    # Extract just the sector convictions section
    sector_start = thesis.find("## Sector Convictions")
    sector_end = thesis.find("## Edge Definition")
    if sector_start > 0 and sector_end > sector_start:
        sector_context = thesis[sector_start:sector_end].strip()
    else:
        sector_context = thesis[:500]

    return (
        "=== CURRENT SECTOR CONVICTIONS ===\n"
        f"{sector_context}\n\n"
        "=== KEY: What We're Looking For ===\n"
        "Revenue-generating protocols with moats, clear value accrual, "
        "reasonable FDV, teams with shipping track records."
    )


def reload_memory() -> None:
    """Clear cached memory files. Call after editing files on disk."""
    load_mandates.cache_clear()
    load_risk_policy.cache_clear()
    load_thesis.cache_clear()
    logger.info("Institutional memory reloaded from disk")


def _load_file(filename: str) -> str:
    """Load a single memory file."""
    filepath = MEMORY_DIR / filename
    if not filepath.exists():
        logger.warning(f"Memory file not found: {filepath}")
        return f"[{filename} not configured]"
    content = filepath.read_text(encoding="utf-8")
    logger.debug(f"Loaded {filename}: {len(content)} chars")
    return content
