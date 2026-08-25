"""Committee Chair: final decision maker for the report and Ray review."""
import json

from app.agents.base import BaseAgent
from app.llm import ModelTier
from app.utils.citations import format_source_catalog_text
from app.utils.types import JSONObject, JSONValue


# --- Prompt budgets -------------------------------------------------------
#
# The Chair used to receive `json.dumps(draft_report, indent=2)[:6000]`. A raw
# byte slice of a 24-section report has two failure modes at once: it drops the
# late sections, and it ends mid-string, so what the Chair actually reads is
# invalid JSON that stops in the middle of a sentence.
#
# Measured against the report shape that agents/report_writer.py requires
# (no production report survives to sample — CONTRACTS §2.3, §2.5 — and
# report_writer runs at max_tokens=8192, i.e. up to ~32,000 characters of JSON):
#
#     chars/section   full report   kept by [:6000]   last section reached
#              300      10,837        55.4%           18_key_risks
#              700      18,837        31.9%           9_team_assessment
#             1400      32,837        18.3%           5_on_chain_metrics
#
# At every plausible size the slice ends before 22_overall_score,
# 23_recommendation and 24_signposts_to_monitor, and at the mid-range size it
# also loses the bull case, the bear case, the key risks and the footnotes the
# citation rules require the Chair to reuse.
#
# The fix is selection, not a bigger number. The budget below is roughly double
# the old one, but the important change is that it is *allocated* across the
# whole document instead of being spent on the front of it, with the
# decision-relevant sections reserved in full and everything else truncated
# per-section behind a visible marker. A section that was shortened says so, so
# the Chair can tell the difference between "brief" and "cut off".
# Sized to pass a full report through intact, not to trim one.
#
# This was 12,000, which fitted the old report exactly — it measured 7,185 chars
# because the Report Writer was never asked for depth. Now that each section
# carries a real brief, a report runs ~40,000 chars, and at the old budget the
# Chair saw 35% of it: the extra depth would have reached the Notion page and the
# HTML report but not the agent that actually makes the decision.
#
# The cost of the larger window is ~7k additional input tokens on one Opus call,
# well under $0.05 a scan. Starving the adjudicator to save that would be a poor
# trade. The cap remains so that a pathological report cannot run unbounded.
#
# Raised again from 48,000 after measuring a real deep report rather than an
# estimate: the live Hyperliquid report's sections are 43,175 chars raw but
# format to 49,048, and six sections were still being cut to the floor. Budget
# against the formatted size, not the raw one.
CHAIR_REPORT_BUDGET_CHARS = 72000
CHAIR_RAY_BUDGET_CHARS = 3000

# The Technical Analyst is in `exclude_from_scores` and reaches the Chair only
# as entry-timing context. docs/CONTRACTS.md §4.1 makes it a defect for it to
# influence conviction, so its budget is deliberately left at the original 2000
# and it is deliberately NOT run through the section-selection machinery: the
# technical payload is small, it already fits, and enlarging or promoting it
# would give it more weight in the prompt than it had before.
CHAIR_TECHNICAL_BUDGET_CHARS = 2000

# Every field gets at least this much before anything is dropped entirely, so
# no part of the report becomes silently invisible the way sections 19-24 were.
_SECTION_FLOOR_CHARS = 240

# Reserved in full first, budget permitting. These are the fields the final
# BUY/PASS/WATCH call actually turns on, and they are the ones the old slice
# reliably destroyed. Order here is allocation priority only — the rendered
# prompt keeps the report's own document order, so this does not re-rank what
# the Chair reads, it only decides what survives when the budget binds.
_REPORT_PRIORITY_KEYS = (
    "25_what_changed",
    "1_executive_summary",
    "23_recommendation",
    "22_overall_score",
    "21_score_breakdown",
    "18_key_risks",
    "17_bear_case",
    "16_bull_case",
    "11_risk_assessment",
    "24_signposts_to_monitor",
    "20_mandate_compliance",
    "19_key_opportunities",
)

_RAY_PRIORITY_KEYS = (
    "rays_verdict",
    "summary",
    "agree_with_committee",
    "risks",
    "inversion_analysis",
    "margin_of_safety",
)


def _stringify(value: JSONValue) -> str:
    """One field as prompt text. Scalars stay scalar; structures stay JSON."""
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return json.dumps(value)
    return json.dumps(value, indent=2, default=str)


def _allocate(lengths: dict[str, int], budget: int, priority: tuple[str, ...]) -> dict[str, int]:
    """How many characters each field may use.

    Priority fields are reserved in full while the budget allows. What is left
    is split evenly across the rest, each guaranteed `_SECTION_FLOOR_CHARS`
    (or its full length, if shorter) so that nothing disappears entirely.
    """
    allowance = {key: 0 for key in lengths}
    remaining = budget

    ordered = [key for key in priority if key in lengths]
    ordered += [key for key in lengths if key not in ordered]

    # Floor first, so a long priority field cannot starve the tail.
    for key in lengths:
        floor = min(lengths[key], _SECTION_FLOOR_CHARS)
        allowance[key] = floor
        remaining -= floor

    if remaining <= 0:
        return allowance

    # Then top the priority fields up to their full length, in priority order.
    for key in ordered:
        want = lengths[key] - allowance[key]
        if want <= 0:
            continue
        give = min(want, remaining)
        allowance[key] += give
        remaining -= give
        if remaining <= 0:
            break

    return allowance


def format_payload_for_prompt(
    payload: JSONObject,
    budget: int,
    priority: tuple[str, ...] = (),
) -> str:
    """Render a dict as budgeted, labelled prompt text.

    Never emits a mid-value cut: each field is truncated on its own and marked,
    so the reader can see that something was shortened rather than receiving a
    document that simply stops. Fields are emitted in the payload's own order.
    """
    if not payload:
        return ""

    rendered = {key: _stringify(value) for key, value in payload.items()}
    total = sum(len(text) for text in rendered.values())

    if total <= budget:
        return "\n\n".join(f"[{key}]\n{text}" for key, text in rendered.items())

    allowance = _allocate({k: len(v) for k, v in rendered.items()}, budget, priority)

    parts = []
    for key, text in rendered.items():
        limit = allowance[key]
        if len(text) <= limit:
            parts.append(f"[{key}]\n{text}")
        else:
            parts.append(
                f"[{key}]\n{text[:limit].rstrip()}\n"
                f"[... {key} truncated for prompt length: {limit} of {len(text)} characters shown ...]"
            )
    return "\n\n".join(parts)


def format_report_for_chair(report: JSONObject, budget: int = CHAIR_REPORT_BUDGET_CHARS) -> str:
    """The committee report as the Chair should read it.

    The 24 sections are budgeted against `_REPORT_PRIORITY_KEYS`; the report's
    surrounding metadata (summary, confidence, footnotes) is emitted alongside
    them so the citation rules still have marker definitions to reuse.

    Note on PROJECT_DECISIONS.md D6: the numbers that appear here are the Report
    Writer's own — `sections.22_overall_score` and the top-level `score`, which
    an LLM was asked to produce and given no weights. They were always part of
    `draft_report` and were always meant to reach the Chair; only the byte slice
    removed them. The orchestrator's deterministic weighted `_calc_score` value
    is a different number and is still never placed in the Chair's context.
    """
    if not report:
        return "No report available"

    sections = report.get("sections")
    if not isinstance(sections, dict):
        # Report Writer fallback shape (orchestrator.py builds this when the
        # writer emitted no `sections` key). It is small; render it whole.
        return format_payload_for_prompt(report, budget)

    meta = {key: value for key, value in report.items() if key != "sections"}
    meta_text = format_payload_for_prompt(meta, max(budget // 4, _SECTION_FLOOR_CHARS))
    section_budget = max(budget - len(meta_text), _SECTION_FLOOR_CHARS * len(sections))
    section_text = format_payload_for_prompt(sections, section_budget, _REPORT_PRIORITY_KEYS)

    return f"{section_text}\n\n--- REPORT METADATA ---\n\n{meta_text}" if meta_text else section_text


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
    # The Chair emits summary, reasoning, an adjudication_trace, signposts and a
    # footnotes array in one JSON object. At 4096 it ran out mid-string on
    # Hyperliquid — the JSON was unparseable, `decision` was lost, and the
    # orchestrator's fallback wrote INSUFFICIENT_DATA into the calibration ledger
    # for a run whose own preamble read "the committee and Ray converge on PASS".
    # A truncated adjudication is not a verdict, and it must not be recorded as
    # one. Sized with room for the deeper report it now reads.
    max_tokens = 16384

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

        report_text = format_report_for_chair(report) if report else "No report available"
        ray_text = (
            format_payload_for_prompt(ray, CHAIR_RAY_BUDGET_CHARS, _RAY_PRIORITY_KEYS)
            if ray
            else "No Ray take available"
        )
        # Unchanged behaviour, deliberately: see CHAIR_TECHNICAL_BUDGET_CHARS
        # and docs/CONTRACTS.md §4.1.
        technical_text = (
            json.dumps(technical_entry, indent=2, default=str)[:CHAIR_TECHNICAL_BUDGET_CHARS]
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
