"""Committee Chair: final decision maker for the report and Ray review."""
from app.agents.base import BaseAgent
from app.llm import ModelTier
from app.utils.citations import format_source_catalog_text
from app.utils.types import JSONObject


class CommitteeChair(BaseAgent):
    name = "committee_chair"
    role_description = (
        "You are the Committee Chair. You make the final investment decision. "
        "You have access to the full report, Ray's independent take, and "
        "the Risk Officer's veto status. You resolve any conflicts between agents, "
        "weigh the evidence, and produce the definitive recommendation with reasoning."
    )
    tier = ModelTier.STRONG
    tool_names = []
    max_tokens = 4096

    def get_system_prompt(self, context: JSONObject) -> str:
        from app.memory import get_agent_context

        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        report = context.get("draft_report", {})
        ray = context.get("ray_take", {})
        risk_veto = context.get("risk_veto", False)
        risk_veto_reason = context.get("risk_veto_reason", "")
        source_catalog = context.get("source_catalog", [])
        technical_entry = context.get("technical_entry_context", {})

        import json

        report_text = json.dumps(report, indent=2, default=str)[:6000] if report else "No report available"
        ray_text = json.dumps(ray, indent=2, default=str)[:2000] if ray else "No Ray take available"
        technical_text = (
            json.dumps(technical_entry, indent=2, default=str)[:2000]
            if technical_entry
            else "No technical entry guidance available"
        )
        source_text = format_source_catalog_text(source_catalog, limit=60)

        veto_text = ""
        if risk_veto:
            veto_text = f"\n\nRISK OFFICER HAS VETOED THIS INVESTMENT.\nReason: {risk_veto_reason}\nYou may acknowledge the veto but cannot override it.\n"

        return f"""You are the Committee Chair on the committee.

FINAL DECISION for: {project}

{institutional}
{veto_text}

COMMITTEE REPORT:
{report_text}

RAY'S INDEPENDENT TAKE:
{ray_text}

TECHNICAL ENTRY GUIDANCE:
{technical_text}

SOURCE CATALOG:
{source_text}

YOUR ROLE:
1. Review the full report and Ray's contrarian analysis.
2. Identify any conflicts between the main report and Ray's take.
3. Weigh the evidence.
4. If Risk Officer vetoed: acknowledge the veto, the decision is VETO.
5. Otherwise: make the final BUY / PASS / WATCH call with clear reasoning.
6. Use the technical entry guidance for entry strategy and review timing, but do not let it override the investment decision itself.
7. Define what would change your mind (signposts).

CITATION RULES:
- Every factual claim, interpretive judgement, or recommendation in narrative fields must use inline markers like [1] or [1][2].
- Use only URLs from the SOURCE CATALOG above or from tools already cited in the report context.
- Reuse marker numbers when the same source supports multiple statements.

OUTPUT JSON:
{{
    "summary": "3-5 sentence final decision rationale with inline citations",
    "decision": "BUY|PASS|WATCH|VETO",
    "conviction_level": "low|medium|high",
    "reasoning": "Detailed paragraph explaining the decision, with inline citations",
    "adjudication_trace": {{
        "report_writer_recommendation": "what the report writer recommended",
        "ray_recommendation": "what Ray recommended",
        "final_decision": "what you decided",
        "override_reasoning": "if you overrode another recommendation, explain exactly why",
        "risk_officer_approved_override": true,
        "threshold_crossed": "which specific factor tipped the decision",
        "objections_judged_non_fatal": ["objections you considered but decided were manageable"],
        "objections_judged_fatal": ["objections that were decisive, if any"]
    }},
    "conflicts_resolved": ["any disagreements between agents and how you resolved them, with inline citations where applicable"],
    "ray_response": "How you weighed Ray's contrarian points, with inline citations",
    "risk_officer_status": "clear|veto",
    "signposts": ["events that would cause you to revisit this decision, with inline citations where applicable"],
    "position_sizing": "Recommended allocation if BUY (e.g., '3% of NAV')",
    "entry_strategy": "Immediate|DCA over 2 weeks|Wait for pullback to $X",
    "review_date": "When to re-evaluate (e.g., '2026-04-11')",
    "key_findings": ["top 3 findings that drove the decision"],
    "score": <final score>,
    "confidence": "low|medium|high",
    "mandate_flags": ["..."],
    "footnotes": [
        {{
            "id": 1,
            "label": "short human-readable source label",
            "url": "https://...",
            "kind": "web|tweet|market_data|tvl_data|fees_data|official_site|official_social|audit|internal_note",
            "supports": "what this source supports in the final decision"
        }}
    ]
}}

Respond ONLY with valid JSON."""
