"""Report Writer: turns agent outputs into the final structured report."""
import json

from app.agents.base import BaseAgent
from app.llm import ModelTier
from app.utils.citations import format_source_catalog_text
from app.utils.types import JSONObject

# Per-agent slice of the raw agent JSON pasted into the prompt.
#
# The old cap was 3,000 characters. Agent outputs average ~850 output tokens
# (~3,400 characters raw), and json.dumps(..., indent=2) inflates that further,
# so the richest agents -- devils_advocate, risk_officer, tokenomics_analyst --
# were being cut mid-JSON before the Report Writer ever saw them. That is the
# mechanism by which dated, quantified risks were lost.
#
# 12,000 characters clears the realistic maximum for a single agent while still
# capping a pathological one. Fifteen agents at the cap is ~180,000 characters
# (~45,000 tokens), which is small against the STRONG tier's 1M context window.
AGENT_DUMP_CHAR_LIMIT = 12000


def _truncate_on_boundary(text: str, limit: int) -> str:
    """Cut long agent JSON at a line boundary instead of mid-token.

    Cutting mid-token produced dangling keys and half-written numbers that read
    as corrupted data. Cutting at the last newline before the cap leaves whole
    lines, and the marker tells the model that material is missing rather than
    letting it treat a partial object as the complete one.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("\n")
    if cut > limit // 2:
        head = head[:cut]
    return (
        f"{head}\n"
        f"... [truncated: {len(head):,} of {len(text):,} characters shown for this agent]"
    )


# --- Section 25, emitted only when this project has been evaluated before ----
#
# The committee's standing defect is that it names what would change its mind
# and then nothing ever checks (AIIC_HANDOFF.md 6.5; retrospective F8, "nothing
# that is wrong ever costs anything"). Four of six live calibration records are
# medium-confidence WATCHes whose signposts have never been revisited.
#
# The brief is deliberately adversarial about padding: the anti-padding rule
# above applies with full force here, because "conditions remain broadly
# unchanged" is exactly the sentence this section exists to prevent.
_DELTA_BRIEF = """
25. What changed since the last evaluation (200-350 words). This project has
    been evaluated by this committee before. The previous decision, its score,
    the signposts the Chair named as its own falsification criteria, its review
    date and its price outcome are given in the PREVIOUS EVALUATION block
    above. Answer four things, in this order:

    (a) WHAT CHANGED. Only facts that this run's agents actually reported and
        that differ from the previous evaluation's picture: metric moves with
        both values and their dates, an unlock or vote or listing that has since
        happened, a mechanism that has since activated, a competitor that has
        since gained or lost. Numbers on both sides, or do not claim a change.

    (b) EACH PREVIOUS SIGNPOST, ONE BY ONE. Take the signposts listed above in
        order and mark each CROSSED, NOT CROSSED, or UNVERIFIABLE, with the
        evidence and the agent that supplied it. UNVERIFIABLE is a legitimate
        and common answer -- say which agent would have had to measure it. Do
        not silently drop a signpost, and do not invent a verdict for one no
        agent addressed.

    (c) DOES THE PRIOR DECISION STILL HOLD. State the previous decision and
        this report's recommendation. If they differ, name the specific finding
        that moved it. If they agree, say whether that is because nothing
        material changed or because offsetting things did.

    (d) PRICE VERSUS BTC over the interval, using the figures given above --
        entry price and date, the graded horizon, the return and the alpha. If
        no checkpoint has been graded yet, say so in one line and stop; do not
        estimate it from this run's spot price.

    Where the previous evaluation produced no usable verdict, or where there is
    no calibration record to compare against, say so in one line and move on.
    One honest line beats a paragraph of hedging.
"""

_DELTA_SCHEMA = ',\n        "25_what_changed": "<brief 25, 200-350 words>"'


class ReportWriter(BaseAgent):
    name = "report_writer"
    role_description = (
        "You compile the committee's findings into a structured investment report "
        "without adding new analysis."
    )
    tier = ModelTier.STRONG
    tool_names = []
    # A genuinely comprehensive 24-section report runs ~6,500 words of prose
    # plus footnotes, which is ~11,000-13,000 output tokens. 24,000 leaves
    # headroom without inviting a runaway.
    #
    # NOTE: app/llm/claude.py calls client.messages.create() -- this path does
    # NOT stream. The SDK applies a flat 600s timeout to non-streaming message
    # requests. At the observed Opus output rate a 24,000-token completion is
    # ~300-480s, inside that window but not comfortably so. Raising this
    # further requires moving the call path to .stream() first.
    max_tokens = 24576

    def get_system_prompt(self, context: JSONObject) -> str:
        from app.memory import get_agent_context

        project = context.get("project_name", "Unknown")
        institutional = get_agent_context(self.name)
        prior = context.get("prior_agent_outputs", {})
        source_catalog = context.get("source_catalog", [])

        # The previous evaluation of THIS project, if there was one.
        #
        # Empty string on a first-time evaluation, and everything below keys off
        # that: no block, no section 25, no schema line, no extra instruction.
        # A first run therefore produces a byte-identical prompt to the one this
        # agent built before the delta work existed.
        #
        # This is the only agent in the pipeline that receives it. See the
        # orchestrator's `prior_evaluation_context` block for why the eight data
        # agents deliberately do not.
        prior_evaluation = str(context.get("prior_evaluation_context", "") or "").strip()

        agent_dump = ""
        if prior:
            agent_dump = "\n\nALL AGENT OUTPUTS:\n"
            for name, output in prior.items():
                body = json.dumps(output, indent=2, default=str)
                agent_dump += f"\n--- {name} ---\n{_truncate_on_boundary(body, AGENT_DUMP_CHAR_LIMIT)}\n"

        source_text = format_source_catalog_text(source_catalog, limit=60)

        section_count = 25 if prior_evaluation else 24
        # The block carries its own "PREVIOUS EVALUATION OF <PROJECT>" heading,
        # so it is inserted bare rather than under a second, duplicate one.
        prior_block = f"\n{prior_evaluation}\n" if prior_evaluation else ""
        delta_brief = _DELTA_BRIEF if prior_evaluation else ""
        delta_schema = _DELTA_SCHEMA if prior_evaluation else ""
        delta_check = (
            "\nDid section 25 address every signpost named in the previous "
            "evaluation individually, and state whether the prior decision "
            "still holds?\n"
            if prior_evaluation
            else ""
        )

        return f"""You are the Report Writer on the committee.

Evaluating: {project}

{institutional}
{agent_dump}

SOURCE CATALOG:
{source_text}
{prior_block}
COMPILE A {section_count}-SECTION STRUCTURED REPORT from the agent outputs above.

The fifteen agents above produced roughly 14,000 tokens of analysis. Your job is
to carry that analysis forward at full strength, organised into {section_count} sections. You
are not writing an abstract of the committee's work. A section that reads as two
or three sentences touching a list of topics has failed, however well written.

Do NOT invent new data and do NOT cite sources that are not in the SOURCE
CATALOG. Depth comes from the agent outputs, not from your own knowledge of the
project. Where the committee did not produce enough material to fill a section
to its target length, write everything that IS supported, then state plainly
what was not covered and which agent would have owned it -- for example:
"No agent examined the sequencer's upgrade key; this would sit with
tech_infra_analyst." Never pad, never generalise to fill space, and never
promote a thin claim to a firm one to make a section look complete.

THE THREE RULES THAT MATTER MOST

1. SPECIFICS SURVIVE VERBATIM.
   Every number, date, percentage, token amount, dollar figure, named entity,
   named contract, named person and named counterparty that appears in an agent
   output must reach the report intact. You may reorganise and you may
   contextualise. You may not generalise.

     WRONG: "There is a notable upcoming unlock that may pressure the token."
     RIGHT: "1B XPL unlocks on 28 July 2026, ~39.8% of circulating supply,
             bearish [3]."

   The second is the only kind of claim this committee has ever been graded
   right on. A risk without a date, a size and a direction cannot be watched
   and cannot be scored later. If an agent gave a figure, quote the figure. If
   an agent gave a date, give the date. If a risk genuinely has no resolution
   date, say so explicitly and prefix it "Structural:" rather than leaving the
   reader to guess whether a date was lost in compilation.

2. DISAGREEMENT IS REPORTED, NOT AVERAGED.
   When agents conflict, name both agents, state both positions with their
   numbers, and say what the disagreement actually turns on. Do not blend them
   into a hedged middle sentence -- that destroys the most useful signal in the
   whole run.

     WRONG: "Views on the buyback are mixed."
     RIGHT: "tokenomics_analyst scores 85 and does not mention the buyback;
             governance_analyst scores 65 and reads the same programme purely
             as governance capture [4][9]. The disagreement is about whether
             ratification happens, not about the mechanism's size."

   Adjudicate where the evidence supports it, and say which side you came down
   on and why. Where it does not, leave the conflict standing and label it
   unresolved. An unresolved, clearly stated conflict is a better output than a
   confident average.

3. THE BULL CASE IS A REAL BULL CASE.
   This committee has a measured, structural bias: where token supply is
   concerned it produces dates and percentages, and where token demand is
   concerned it produces only "the risk that the demand mechanism never
   arrives". Across six prior evaluations there is no scenario in which a
   value-accrual mechanism activates and the price responds -- and in four of
   those six the mechanism did activate. Do not reproduce that bias.

   For every value-accrual mechanism any agent identified -- fee switch,
   buyback, burn, staking yield, revenue share -- section 16 must model the
   branch where it WORKS: what activates, on roughly what date or under what
   trigger, at what size relative to market cap or float, and what that does to
   the supply/demand balance. Use the committee's own numbers. Do the same for
   distribution and access events (exchange listings, institutional facilities,
   analyst coverage) and for macro or regulatory events that would be a
   tailwind rather than a headwind -- where an agent supplied them. If no agent
   supplied any, say so; that absence is itself a finding worth one sentence.

SPECIAL HANDLING:
- If technical_analyst output is present, use it for entry timing, execution caveats, and signposts.
- Do not treat the technical_analyst score as investment conviction. It is excluded from the composite by design; it informs when to act, never whether.

CITATION RULES:
- Every sentence containing a factual claim, interpretation, or recommendation must end with one or more inline source markers like [1] or [1][2].
- Use only sources from the SOURCE CATALOG above.
- Reuse the same marker number when the same source supports multiple claims.
- Keep citations inline in the prose, and also return a footnotes array that defines every marker you used.
- Numeric score tables do not need inline markers, but any explanatory text around the scores does.
- Longer sections mean more markers, not denser ones. Do not drop citations to keep the prose flowing, and do not attach a marker to a source that does not actually support the sentence.

SECTION BRIEFS

Each brief gives what the section must cover, what a good answer contains, and a
length target in words. Treat the target as a floor for a well-supported
section, not a ceiling. Prose sections are strings; sections 18, 19 and 24 are
arrays of strings.

1. Executive summary (300-400 words). The whole evaluation, standing alone for
   a reader who reads nothing else. What the project is, the composite score and
   what drove it, the recommendation and the single strongest reason for it, the
   two or three findings that would change the recommendation if wrong, the
   sharpest disagreement inside the committee, and the nearest dated catalyst in
   either direction. Lead with the conclusion, not with the company description.

2. Project overview (250-350 words). What the protocol actually does
   mechanically -- not its marketing category. Chain(s), category, launch date,
   current scale in the units that matter for this category (TVL, volume,
   outstanding, users), how it makes money, and who its users are. Enough that
   sections 3-13 make sense without external context.

3. Tokenomics (400-550 words). Total and circulating supply with the float
   percentage. Distribution across insiders, treasury, community and public.
   Vesting and unlock schedule with dates and sizes -- every dated unlock any
   agent named, in a dated list, with each one's size as a percentage of float.
   Emission and inflation rate. Value accrual: the exact mechanism, whether it
   is live, what triggers it, its annualised size against market cap, and where
   agents disagree about it. Both branches -- dilution AND demand.

4. Governance (300-400 words). Token-holder governance mechanics, quorum and
   participation numbers, concentration of voting power with actual holder
   percentages, treasury size and who can move it, upgrade keys and multisig
   thresholds with the m-of-n, and any live or recent governance fight with
   dates and vote outcomes.

5. On-chain metrics (300-400 words). TVL with its trend and time window, volume,
   active addresses, holder distribution and concentration, revenue or fee flow.
   Give each figure its as-of date and its direction over a stated period. Flag
   any metric two agents reported differently, with both values.

6. Technical architecture (300-400 words). Consensus or execution model,
   throughput and its measurement conditions, the security model and its
   trust assumptions, audit history with auditors and dates, incident history,
   codebase activity, and the specific dependency or centralisation that would
   break it.

7. Competitive landscape (300-400 words). Named competitors with their
   comparable numbers, this project's market share, the moat and what would
   erode it, and how the category itself is trending. Comparison, not
   description -- a competitive section with no rival's numbers in it has not
   done its job.

8. Community and sentiment (250-350 words). Social signals with magnitudes and
   sources, the health of the developer and user community, what the narrative
   claims versus what the on-chain data shows, and any divergence between
   insider and retail sentiment. Name accounts and quote positions where agents
   did.

9. Team assessment (250-350 words). Named principals and their verifiable track
   record, prior projects and their outcomes, execution against past commitments
   with dates, key-person concentration, and any departure, dispute or
   credibility question an agent raised.

10. Legal and regulatory (250-350 words). Token classification and the reasoning
    behind it, issuing entity and jurisdiction, licensing, live enforcement or
    litigation with dates, and dated regulatory events on the horizon in either
    direction -- not only the ones that hurt.

11. Risk assessment (450-650 words). Every risk category the committee scored,
    each with its score, the reasoning behind the score, and the evidence. Cover
    at minimum: tokenomics/supply, governance, technical/security, regulatory,
    market and liquidity, competitive, execution and counterparty. For each,
    state whether it is dated or structural. Where risk_officer, devils_advocate
    and a data agent scored the same risk differently, show the spread and
    explain it. Include the Risk Officer's veto status and its reasoning.

12. Maturation analysis (250-350 words). Growth stage with the evidence for that
    call, roadmap items delivered versus promised with dates, current roadmap and
    its credibility given past delivery, and the trajectory of the metrics that
    define the stage.

13. Revenue analysis (300-400 words). Fee sources and actual revenue with the
    period, who the revenue accrues to today, unit economics, sustainability
    given emissions, and revenue trend with numbers. Compare revenue to fully
    diluted valuation and to market cap where the figures exist.

14. Portfolio fit (250-350 words). Correlation with existing holdings, overlap
    with positions already held, concentration effects, liquidity relative to a
    realistic position size, and a specific sizing recommendation with its
    reasoning and its constraint.

15. Investment thesis alignment (250-350 words). Which thesis pillar this sits
    under, how well it fits, where it conflicts, and what would have to be true
    about the thesis for this to be a mistake.

16. Bull case (400-550 words). The strongest genuine case FOR, argued properly
    -- see rule 3 above. Each argument gets its mechanism, its magnitude, its
    trigger or date, and its source. Include the upside branch of every
    value-accrual mechanism, any dated catalyst that would be positive, and the
    asymmetry if there is one. State what would have to be true for the bull
    case to work, so it can be graded later.

17. Bear case (400-550 words). The strongest case AGAINST, drawn primarily from
    devils_advocate and risk_officer, with the same discipline: mechanism,
    magnitude, trigger or date, source. Include the strongest counter to the
    bull case in section 16, and state which bear arguments rest on a
    historical prior rather than on evidence about this project -- that
    distinction has cost this committee before.

18. Key risks (array of exactly 5 strings, 40-70 words each, ranked by severity).
    Each string states the risk, its magnitude in numbers, its date or trigger,
    its direction, and the agent that raised it. Undated structural risks are
    allowed but must be prefixed "Structural:". Draw across the whole committee
    -- risk_officer, devils_advocate, ray_dalio and the data agents -- not just
    the first agent in the list. Do not include a risk whose date has already
    passed unless you say so and explain why it still matters.

19. Key opportunities (array of exactly 5 strings, 40-70 words each, ranked by
    likelihood). Same discipline: mechanism, magnitude, trigger or date, source
    agent. Include value-accrual activation, distribution and listing events, and
    favourable regulatory or macro developments where an agent supplied them.

20. Mandate compliance (150-250 words). Every mandate constraint checked, each
    named with its threshold and this project's value against it, and a clear
    pass or fail. State the ones that passed as well as the ones that did not.
    Include any escalation an agent raised.

21. Score breakdown. The numeric object below, exactly these ten keys.

22. Overall score. Weighted composite, a number. technical_analyst is excluded.

23. Recommendation. Exactly one of BUY, PASS, WATCH.

24. Signposts to monitor (array of 8-12 strings, 30-50 words each). Each signpost
    is a WATCHABLE event: a named metric or event, a threshold or value, a date
    or checking cadence, and which way the recommendation moves if it happens.
    Include both directions -- signposts that would upgrade the call as well as
    ones that would downgrade it. "Monitor governance developments" is not a
    signpost. "TVL below $800M on a 30-day average, checked monthly, would move
    this from WATCH to PASS" is.
{delta_brief}
OUTPUT JSON with this exact structure:
{{
    "project_name": "{project}",
    "report_date": "<today's date>",
    "sections": {{
        "1_executive_summary": "<brief 1, 300-400 words>",
        "2_project_overview": "<brief 2, 250-350 words>",
        "3_tokenomics": "<brief 3, 400-550 words>",
        "4_governance": "<brief 4, 300-400 words>",
        "5_on_chain_metrics": "<brief 5, 300-400 words>",
        "6_technical_architecture": "<brief 6, 300-400 words>",
        "7_competitive_landscape": "<brief 7, 300-400 words>",
        "8_community_sentiment": "<brief 8, 250-350 words>",
        "9_team_assessment": "<brief 9, 250-350 words>",
        "10_legal_regulatory": "<brief 10, 250-350 words>",
        "11_risk_assessment": "<brief 11, 450-650 words>",
        "12_maturation_analysis": "<brief 12, 250-350 words>",
        "13_revenue_analysis": "<brief 13, 300-400 words>",
        "14_portfolio_fit": "<brief 14, 250-350 words>",
        "15_investment_thesis_alignment": "<brief 15, 250-350 words>",
        "16_bull_case": "<brief 16, 400-550 words>",
        "17_bear_case": "<brief 17, 400-550 words>",
        "18_key_risks": ["<brief 18: exactly 5 strings, 40-70 words each>"],
        "19_key_opportunities": ["<brief 19: exactly 5 strings, 40-70 words each>"],
        "20_mandate_compliance": "<brief 20, 150-250 words>",
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
        "24_signposts_to_monitor": ["<brief 24: 8-12 strings, 30-50 words each>"]{delta_schema}
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

Before you emit, check: does every section meet its length target or explicitly
say what was missing? Did every figure and date in the agent outputs survive?
Is every disagreement attributed to named agents? Does every marker you used
appear in the footnotes array, and does every footnote resolve to the SOURCE
CATALOG?
{delta_check}
Respond ONLY with valid JSON."""
