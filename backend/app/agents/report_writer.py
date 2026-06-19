"""Report Writer: turns agent outputs into the final structured report."""
import json

from app.agents.base import BaseAgent
from app.llm import ModelTier
from app.utils.citations import format_source_catalog_text
from app.utils.types import JSONObject


class ReportWriter(BaseAgent):
    name = "report_writer"
    role_description = (
        "You compile the committee's findings into a structured investment report "
        "without adding new analysis."
    )
    tier = ModelTier.STRONG
    tool_names = []
    max_tokens = 8192

    def get_system_prompt(self, context: JSONObject) -> str:
        from app.memory import get_agent_context

        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        prior = context.get("prior_agent_outputs", {})
        source_catalog = context.get("source_catalog", [])

        agent_dump = ""
        if prior:
            agent_dump = "\n\nALL AGENT OUTPUTS:\n"
            for name, output in prior.items():
                agent_dump += f"\n--- {name} ---\n{json.dumps(output, indent=2, default=str)[:3000]}\n"

        source_text = format_source_catalog_text(source_catalog, limit=60)

        return f"""You are the Report Writer on the committee.

Evaluating: {project}

{institutional}
{agent_dump}

SOURCE CATALOG:
{source_text}

COMPILE A 24-SECTION STRUCTURED REPORT from the agent outputs above.
Do NOT invent new data and do NOT cite sources that are not in the SOURCE CATALOG.

SPECIAL HANDLING:
- If technical_analyst output is present, use it for entry timing, execution caveats, and signposts.
- Do not treat the technical_analyst score as investment conviction.

CITATION RULES:
- Every sentence containing a factual claim, interpretation, or recommendation must end with one or more inline source markers like [1] or [1][2].
- Use only sources from the SOURCE CATALOG above.
- Reuse the same marker number when the same source supports multiple claims.
- Keep citations inline in the prose, and also return a footnotes array that defines every marker you used.
- Numeric score tables do not need inline markers, but any explanatory text around the scores does.

OUTPUT JSON with this exact structure:
{{
    "project_name": "{project}",
    "report_date": "<today's date>",
    "sections": {{
        "1_executive_summary": "3-5 sentence overview of the entire evaluation",
        "2_project_overview": "What the project does, category, chain, founding",
        "3_tokenomics": "Supply, distribution, vesting, inflation, value accrual",
        "4_governance": "DAO structure, voting, decentralisation, treasury",
        "5_on_chain_metrics": "TVL, tx volume, active addresses, holder distribution",
        "6_technical_architecture": "Consensus, throughput, security model, codebase",
        "7_competitive_landscape": "Market position, SWOT, moat assessment",
        "8_community_sentiment": "Social signals, community health, narrative vs reality",
        "9_team_assessment": "Track record, key persons, execution history",
        "10_legal_regulatory": "Token classification, jurisdiction, compliance",
        "11_risk_assessment": "All risk categories with scores",
        "12_maturation_analysis": "Growth stage, roadmap execution, trajectory",
        "13_revenue_analysis": "Fees, revenue, sustainability, unit economics",
        "14_portfolio_fit": "Diversification, correlation, sizing recommendation",
        "15_investment_thesis_alignment": "How this aligns with current thesis",
        "16_bull_case": "Strongest arguments for investment",
        "17_bear_case": "Strongest arguments against (from Devil's Advocate)",
        "18_key_risks": "Top 5 risks ranked by severity",
        "19_key_opportunities": "Top 5 opportunities ranked by likelihood",
        "20_mandate_compliance": "Any mandate violations or constraints triggered",
        "21_score_breakdown": {{
            "tokenomics": <score>,
            "governance": <score>,
            "on_chain": <score>,
            "tech": <score>,
            "competitive": <score>,
            "sentiment": <score>,
            "risk": <score>,
            "maturation": <score>,
            "legal": <score>,
            "portfolio_fit": <score>
        }},
        "22_overall_score": <weighted average>,
        "23_recommendation": "BUY|PASS|WATCH",
        "24_signposts_to_monitor": ["list of events/metrics to track that would change the recommendation"]
    }},
    "summary": "Final 2-sentence summary",
    "score": <overall score>,
    "confidence": "low|medium|high",
    "data_sources": ["all tools used across agents"],
    "footnotes": [
        {{
            "id": 1,
            "label": "short human-readable source label",
            "url": "https://...",
            "kind": "web|tweet|market_data|tvl_data|fees_data|official_site|official_social|audit|internal_note",
            "supports": "what this source supports in the report"
        }}
    ]
}}

Respond ONLY with valid JSON."""
