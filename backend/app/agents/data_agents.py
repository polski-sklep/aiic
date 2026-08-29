"""Data-gathering agents — run in parallel during Step 1.

These are the Sonnet-tier agents that collect and analyse domain-specific data.
"""
from app.agents.base import BaseAgent
from app.llm import ModelTier


class GovernanceAnalyst(BaseAgent):
    name = "governance_analyst"
    role_description = (
        "You evaluate governance structures: DAO design, voting mechanisms, "
        "decentralisation quality, proposal history, voter participation, "
        "treasury management, multisig configuration, delegation patterns, "
        "and emergency powers. You identify centralisation risks and governance capture."
    )
    tier = ModelTier.BALANCED
    tool_names = ["get_tvl", "web_search"]


class OnChainAnalyst(BaseAgent):
    name = "onchain_analyst"
    role_description = (
        "You examine on-chain data: transaction volume, active addresses, TVL trends, "
        "liquidity depth, smart contract audit status, holder distribution, whale activity, "
        "developer activity on GitHub, gas usage patterns, and MEV exposure. "
        "You detect anomalies, wash trading, and fake usage metrics."
    )
    tier = ModelTier.BALANCED
    tool_names = ["get_price", "get_token_info", "get_tvl", "web_search"]


class TechInfraAnalyst(BaseAgent):
    name = "tech_infra_analyst"
    role_description = (
        "You evaluate technical architecture: consensus mechanism, throughput/TPS, "
        "finality time, security model, upgrade mechanism, codebase quality, "
        "developer tooling, node operator requirements, technical debt, "
        "and the credibility of the technical roadmap. For L1s/L2s, you assess "
        "the execution environment, data availability, and proving system."
    )
    tier = ModelTier.BALANCED
    tool_names = ["web_search"]


class CompetitiveIntel(BaseAgent):
    name = "competitive_intel"
    role_description = (
        "You analyse competitive positioning: market share within category, "
        "SWOT analysis, moat assessment (network effects, switching costs, data advantages), "
        "comparable protocol metrics (TVL, revenue, users), and market timing. "
        "You identify whether the project is a category leader, fast follower, or late entrant."
    )
    tier = ModelTier.BALANCED
    tool_names = ["get_price", "get_tvl", "get_protocol_fees", "get_category_peers", "web_search"]


class FieldIntel(BaseAgent):
    name = "field_intel"
    role_description = (
        "You gather qualitative intelligence: social media sentiment (Twitter/X, Discord, Telegram), "
        "community health metrics (growth rate, engagement quality, developer community), "
        "recent news and announcements, partnership signals, and the gap between "
        "narrative/hype and actual product reality. You separate signal from noise."
    )
    tier = ModelTier.BALANCED
    tool_names = ["web_search", "search_twitter"]


class LegalRegulatory(BaseAgent):
    name = "legal_regulatory"
    role_description = (
        "You assess legal and regulatory positioning: token classification risk "
        "(is this a security?), jurisdiction of entity, compliance posture, "
        "regulatory actions or investigations, sanctions exposure, "
        "legal entity structure (foundation, DAO wrapper, offshore), "
        "and the project's proactive engagement with regulators."
    )
    tier = ModelTier.BALANCED
    tool_names = ["web_search"]
