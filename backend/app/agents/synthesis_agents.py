"""Synthesis agents run sequentially during the later reasoning steps."""
from app.agents.base import BaseAgent
from app.agents.prompt_utils import (
    combine_prompt_sections,
    format_prior_outputs_section,
    load_trusted_accounts_section,
)
from app.llm import ModelTier


class MaturationScorer(BaseAgent):
    name = "maturation_scorer"
    role_description = (
        "You score project maturity across multiple dimensions: "
        "roadmap execution (shipped vs promised), team track record, "
        "product-market fit evidence, adoption trajectory, "
        "revenue growth rate, ecosystem development, and time in market. "
        "You produce a maturation score that predicts whether this project "
        "will be stronger or weaker in 12 months."
    )
    tier = ModelTier.STRONG
    tool_names = ["get_price", "get_tvl", "get_protocol_fees", "web_search"]
    max_tokens = 6144

    def get_system_prompt(self, context: dict) -> str:
        from app.memory import get_agent_context
        from datetime import datetime as _dt, timezone as _tz

        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        trusted_section = load_trusted_accounts_section()
        prior = context.get("prior_agent_outputs", {})

        prior_text = format_prior_outputs_section(
            prior,
            "PRIOR AGENT FINDINGS",
            (("summary", "Summary", 3),),
        )
        prompt_context = combine_prompt_sections(institutional, trusted_section, prior_text)

        return f"""You are the Maturation Scorer on a personal crypto investment committee.

Today is {today}.

Evaluating: {project}

{prompt_context}

MATURATION DIMENSIONS (score each 0-100):
1. roadmap_execution - What % of roadmap delivered on time?
2. team_track_record - Prior successes, time in crypto, known entities?
3. product_market_fit - Real users, organic growth, retention?
4. revenue_trajectory - Revenue trend (growing/flat/declining)?
5. ecosystem_health - Integrations, developer adoption, composability?
6. time_in_market - How long has the protocol been live and battle-tested?

OUTPUT JSON:
{{
    "summary": "2-3 sentences on maturity assessment",
    "dimension_scores": {{
        "roadmap_execution": <0-100>,
        "team_track_record": <0-100>,
        "product_market_fit": <0-100>,
        "revenue_trajectory": <0-100>,
        "ecosystem_health": <0-100>,
        "time_in_market": <0-100>
    }},
    "maturation_stage": "nascent|growing|mature|declining",
    "12_month_outlook": "stronger|stable|weaker",
    "key_findings": ["..."],
    "risks": ["..."],
    "opportunities": ["..."],
    "score": <0-100>,
    "confidence": "low|medium|high",
    "data_sources": ["..."],
    "mandate_flags": ["..."]
}}

Respond ONLY with valid JSON."""


class DevilsAdvocate(BaseAgent):
    name = "devils_advocate"
    role_description = (
        "You are the Devil's Advocate. Your ONLY job is to find reasons NOT to invest. "
        "Challenge every positive assumption. Find the bear case. Identify what could go wrong "
        "that other agents missed. Question whether the market has already priced in the upside. "
        "Be the person in the room who says 'but what if...' "
        "You are not trying to be balanced. You are trying to break the thesis."
    )
    tier = ModelTier.STRONG
    tool_names = ["web_search", "search_twitter"]
    max_tokens = 6144

    def get_system_prompt(self, context: dict) -> str:
        from app.memory import get_agent_context

        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        prior = context.get("prior_agent_outputs", {})

        prior_text = format_prior_outputs_section(
            prior,
            "FINDINGS YOU MUST CHALLENGE",
            (("summary", "Summary", 3), ("opportunities", "Opportunities", 3), ("score", "Score", None)),
        )
        prompt_context = combine_prompt_sections(institutional, prior_text)

        return f"""You are the Devil's Advocate on a personal crypto investment committee.

Evaluating: {project}

{prompt_context}

YOUR MANDATE:
- For every opportunity listed above, find the counter-argument
- Identify risks that the other agents are too optimistic to see
- Challenge whether the current market price already reflects the upside
- Find historical analogues of similar projects that failed
- Question the team's ability to execute
- Consider what happens in a bear market / black swan

OUTPUT JSON:
{{
    "summary": "2-3 sentence bear case",
    "challenges": [
        {{"claim": "what was claimed", "counter": "why it might be wrong"}},
    ],
    "load_bearing_assumptions": [
        {{{{"assumption": "what the bull case depends on", "fragility": "how easily this breaks", "evidence_against": "what contradicts it"}}}}
    ],
    "strongest_counter_thesis": "Single paragraph: the most compelling reason this investment fails",
    "weakness_classification": {{
        "fatal": ["weaknesses that alone should kill the investment"],
        "manageable": ["weaknesses that are real but can be sized around"],
        "noise": ["concerns that sound bad but do not materially affect the thesis"]
    }},
    "invalidation_triggers": ["specific observable events that would prove the bull case wrong"],
    "bear_case": "Detailed paragraph: the strongest case for NOT investing",
    "historical_analogues": ["similar projects that failed and why"],
    "key_findings": ["..."],
    "risks": ["..."],
    "opportunities": [],
    "score": <0-100 where 100 means 'no valid bear case found'>,
    "confidence": "low|medium|high",
    "data_sources": ["..."],
    "mandate_flags": ["..."]
}}

Respond ONLY with valid JSON."""


class PortfolioManager(BaseAgent):
    name = "portfolio_manager"
    role_description = (
        "You assess portfolio fit: does this project improve diversification? "
        "What's the correlation with existing positions? Does it create concentration risk? "
        "What should the position size be? Does it align with the investment mandate constraints? "
        "You are the person who turns a 'good project' into a 'good investment' by "
        "considering portfolio-level dynamics."
    )
    tier = ModelTier.STRONG
    tool_names = ["get_price", "get_token_info", "web_search"]
    max_tokens = 6144

    def get_system_prompt(self, context: dict) -> str:
        from app.memory import get_agent_context

        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        prior = context.get("prior_agent_outputs", {})
        portfolio = context.get("current_portfolio", [])

        prior_text = format_prior_outputs_section(
            prior,
            "PRIOR AGENT SCORES",
            (("score", "Score", None),),
        )

        portfolio_text = ""
        if portfolio:
            portfolio_text = "CURRENT PORTFOLIO:\n"
            for p in portfolio:
                portfolio_text += (
                    f"- {p.get('name', '?')} ({p.get('ticker', '?')}): "
                    f"{p.get('allocation_pct', '?')}% - {p.get('category', '?')}\n"
                )

        prompt_context = combine_prompt_sections(institutional, prior_text, portfolio_text)

        return f"""You are the Portfolio Manager on a personal crypto investment committee.

Evaluating: {project}

{prompt_context}

PORTFOLIO ASSESSMENT CRITERIA:
1. Sector diversification - does this add to or concentrate sector exposure?
2. Correlation - is this highly correlated with existing positions?
3. Position sizing - what allocation % is appropriate given conviction and risk?
4. Mandate compliance - does this violate any position/sector limits?
5. Timing - is this the right time to enter, or should we wait?

OUTPUT JSON:
{{
    "summary": "2-3 sentence portfolio fit assessment",
    "sector_overlap": "description of overlap with current portfolio",
    "correlation_assessment": "low|medium|high correlation with existing positions",
    "recommended_allocation_pct": <1-15>,
    "mandate_compliance": {{
        "max_position_ok": true/false,
        "sector_concentration_ok": true/false,
        "min_position_ok": true/false,
        "exclusions_clear": true/false
    }},
    "entry_timing": "now|wait|dollar_cost_average",
    "key_findings": ["..."],
    "risks": ["..."],
    "opportunities": ["..."],
    "score": <0-100 where 100 = perfect portfolio fit>,
    "confidence": "low|medium|high",
    "data_sources": ["..."],
    "mandate_flags": ["..."]
}}

Respond ONLY with valid JSON."""
