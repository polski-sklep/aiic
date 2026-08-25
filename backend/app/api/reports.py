"""Report endpoints: markdown, server-rendered HTML, and the report index.

Security note (see docs/reviews/security-review.md):
The markdown this module renders is LLM-authored and embeds text fetched from
the open internet (Brave web search, X/Twitter, Notion). It is hostile input.

Three independent defences apply to the HTML path:

1. The page contains **no JavaScript at all**. Markdown is rendered to HTML in
   Python; nothing is assigned to `innerHTML` in a browser.
2. Rendering is **escape-first**: every leaf string passes through
   `html.escape(..., quote=True)` before any markup is emitted, and tags are
   only ever produced by this module. Untrusted text can therefore never
   become markup — safe by construction, not by filtering.
3. The response carries a restrictive `Content-Security-Policy` with
   `default-src 'none'` and no `script-src`, so even a defect in (2) cannot
   result in script execution.

URLs are additionally scheme-allowlisted (`http`, `https`, `mailto`), which
rejects `javascript:` and `data:` hrefs.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentOutput, Evaluation, Project
from app.utils.citations import normalize_footnotes, reindex_citations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "tpl.html"

# No scripts, no external anything. Inline <style> is the only exception.
CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

SECTION_TITLES: dict[str, str] = {
    "1_executive_summary": "Executive Summary",
    # Emitted by report_writer only when this project has been evaluated
    # before, so it is absent from every report written to date and from every
    # first-time evaluation. `sections.get(key, "")` yields "" for those and the
    # loop below skips falsy content, so adding the key here cannot change what
    # an existing report renders — verified by re-rendering the persisted
    # Hyperliquid report to an identical sha256 either way.
    #
    # Placed second on purpose: on a re-evaluation the delta against the last
    # call is what a reader wants immediately after the summary. Dict order is
    # presentation order only; the "25" in the key is its position in the Report
    # Writer's schema, not on the page.
    "25_what_changed": "What Changed Since Last Evaluation",
    "2_project_overview": "Project Overview",
    "3_tokenomics": "Tokenomics",
    "4_governance": "Governance",
    "5_on_chain_metrics": "On-Chain Metrics",
    "6_technical_architecture": "Technical Architecture",
    "7_competitive_landscape": "Competitive Landscape",
    "8_community_sentiment": "Community & Sentiment",
    "9_team_assessment": "Team Assessment",
    "10_legal_regulatory": "Legal & Regulatory",
    "11_risk_assessment": "Risk Assessment",
    "12_maturation_analysis": "Maturation Analysis",
    "13_revenue_analysis": "Revenue Analysis",
    "14_portfolio_fit": "Portfolio Fit",
    "15_investment_thesis_alignment": "Thesis Alignment",
    "16_bull_case": "Bull Case",
    "17_bear_case": "Bear Case",
}

# Postgres `jsonb` does not preserve object key order — it stores keys sorted by
# length then bytes — so score rows arrive in an arbitrary order that has
# nothing to do with the Report Writer's schema. Impose the schema order.
SCORE_DOMAIN_ORDER: tuple[str, ...] = (
    "tokenomics", "governance", "on_chain", "tech", "competitive",
    "sentiment", "risk", "maturation", "legal", "portfolio_fit",
)


def _ordered_scores(scores: Any) -> dict[str, Any]:
    if not isinstance(scores, dict):
        return {}
    rank = {name: i for i, name in enumerate(SCORE_DOMAIN_ORDER)}
    return dict(
        sorted(scores.items(), key=lambda kv: (rank.get(str(kv[0]), len(rank)), str(kv[0])))
    )


RAY_FIELDS: list[tuple[str, str]] = [
    ("inversion_analysis", "Inversion (How do we lose money?)"),
    ("circle_of_competence", "Circle of Competence"),
    ("margin_of_safety", "Margin of Safety"),
    ("incentive_analysis", "Incentive Analysis"),
    ("stupidity_check", "Stupidity Check"),
]

# Agents whose absence materially changes what the report can say.
CORE_AGENTS = ("report_writer", "committee_chair", "ray_dalio")

# decision -> (css class, glyph, accessible name). The glyph carries the
# meaning in greyscale and for colour-blind readers; colour is never the only
# channel.
DECISION_STYLES: dict[str, tuple[str, str, str]] = {
    "BUY": ("buy", "▲", "Buy"),
    "WATCH": ("watch", "◆", "Watch"),
    "PASS": ("pass", "▬", "Pass"),
    "VETO": ("veto", "✕", "Veto"),
    "INSUFFICIENT_DATA": ("unknown", "?", "Insufficient data"),
}


# --------------------------------------------------------------------------
# Extraction: agent_outputs -> a normalised, renderer-agnostic document
# --------------------------------------------------------------------------


@dataclass
class ReportParts:
    """Everything both renderers need, extracted exactly once.

    Citation reindexing is order-sensitive (footnote numbers are assigned on
    first use), so extraction must happen once and feed both the markdown and
    the HTML renderer. That is the reason this type exists.
    """

    project_name: str
    ticker: str = ""
    evaluation_id: str = ""
    completed_at: str = ""
    report_date: str = ""
    decision: str = ""
    conviction: str = ""
    sizing: str = ""
    entry: str = ""
    review: str = ""
    chair_reasoning: Any = ""
    has_chair: bool = False
    vetoed: bool = False
    veto_reason: Any = ""
    overall_score: Any = "N/A"
    scores: Any = field(default_factory=dict)
    sections: list[tuple[str, str, Any]] = field(default_factory=list)
    risks: Any = None
    opportunities: Any = None
    mandate: Any = ""
    ray: dict[str, Any] | None = None
    signposts: Any = None
    conflicts: Any = None
    footnotes: list[dict[str, Any]] = field(default_factory=list)
    agents_seen: list[str] = field(default_factory=list)
    agents_errored: list[str] = field(default_factory=list)
    agents_missing: list[str] = field(default_factory=list)


def _extract_report_parts(project_name: str, agent_outputs: list[dict]) -> ReportParts:
    """Normalise raw agent outputs into a single renderable document."""
    report_data = None
    chair_data = None
    ray_data = None
    risk_data = None

    seen: list[str] = []
    errored: list[str] = []

    for ao in agent_outputs:
        name = ao.get("agent_name", "")
        seen.append(name)
        if ao.get("error"):
            errored.append(name)
        if name == "report_writer":
            report_data = ao.get("output", {})
        elif name == "committee_chair":
            chair_data = ao.get("output", {})
        elif name == "ray_dalio":
            ray_data = ao.get("output", {})
        elif name == "risk_officer":
            risk_data = ao.get("output", {})

    sections = report_data.get("sections", {}) if isinstance(report_data, dict) else {}
    if not isinstance(sections, dict):
        sections = {}
    merged_footnotes: list[dict] = []

    def _fn(data, key="footnotes"):
        return normalize_footnotes(data.get(key, []) if isinstance(data, dict) else [])

    report_footnotes = _fn(report_data)
    chair_footnotes = _fn(chair_data)
    ray_footnotes = _fn(ray_data)
    risk_footnotes = _fn(risk_data)

    def cited_text(value, footnotes):
        if isinstance(value, str):
            return reindex_citations(value, footnotes, merged_footnotes)[0]
        if isinstance(value, list):
            remapped = []
            for item in value:
                if isinstance(item, str):
                    remapped.append(reindex_citations(item, footnotes, merged_footnotes)[0])
                else:
                    remapped.append(item)
            return remapped
        return value

    today = datetime.utcnow().strftime("%Y-%m-%d")
    parts = ReportParts(project_name=project_name)
    parts.report_date = str(
        (report_data.get("report_date", today) if isinstance(report_data, dict) else today) or today
    )

    # Order of the cited_text() calls below fixes footnote numbering. Keep it
    # stable or previously issued reports renumber.
    if chair_data:
        parts.has_chair = True
        parts.decision = str(chair_data.get("decision", "N/A"))
        parts.conviction = str(chair_data.get("conviction_level", "N/A"))
        parts.sizing = str(chair_data.get("position_sizing", "N/A"))
        parts.entry = str(chair_data.get("entry_strategy", "N/A"))
        parts.review = str(chair_data.get("review_date", "N/A"))
        parts.chair_reasoning = cited_text(chair_data.get("reasoning", "N/A"), chair_footnotes)

    parts.scores = _ordered_scores(sections.get("21_score_breakdown", {})) or sections.get(
        "21_score_breakdown", {}
    )
    parts.overall_score = sections.get("22_overall_score", "N/A")

    for key, title in SECTION_TITLES.items():
        content = cited_text(sections.get(key, ""), report_footnotes)
        if content:
            parts.sections.append((key, title, content))

    parts.risks = cited_text(sections.get("18_key_risks", []), report_footnotes)
    parts.opportunities = cited_text(sections.get("19_key_opportunities", []), report_footnotes)
    parts.mandate = cited_text(sections.get("20_mandate_compliance", ""), report_footnotes)

    if ray_data:
        ray: dict[str, Any] = {
            "verdict": cited_text(ray_data.get("rays_verdict", "N/A"), ray_footnotes),
            "summary": cited_text(ray_data.get("summary", "N/A"), ray_footnotes),
            "fields": [],
        }
        for fname, label in RAY_FIELDS:
            val = cited_text(ray_data.get(fname, ""), ray_footnotes)
            if val:
                ray["fields"].append((label, val))
        parts.ray = ray

    signposts = cited_text(sections.get("24_signposts_to_monitor", []), report_footnotes)
    if not signposts and chair_data:
        signposts = cited_text(chair_data.get("signposts", []), chair_footnotes)
    parts.signposts = signposts

    if chair_data and chair_data.get("conflicts_resolved"):
        parts.conflicts = cited_text(chair_data.get("conflicts_resolved", []), chair_footnotes)

    # Risk veto. Reindexed LAST so that adding veto rendering cannot renumber
    # any footnote the previous implementation already emitted.
    vetoed = False
    veto_reason = ""
    if isinstance(risk_data, dict):
        vetoed = bool(risk_data.get("veto", False))
        veto_reason = str(risk_data.get("veto_reason") or "")
    if not vetoed and isinstance(chair_data, dict):
        vetoed = str(chair_data.get("risk_officer_status", "")).lower() == "veto"
    if not vetoed and parts.decision.upper() == "VETO":
        vetoed = True
    parts.vetoed = vetoed
    parts.veto_reason = cited_text(veto_reason, risk_footnotes) if veto_reason else ""

    parts.footnotes = merged_footnotes
    parts.agents_seen = seen
    parts.agents_errored = errored
    parts.agents_missing = [a for a in CORE_AGENTS if a not in seen]
    return parts


# --------------------------------------------------------------------------
# Markdown renderer (the /markdown endpoint's output contract)
# --------------------------------------------------------------------------


def _render_markdown(parts: ReportParts) -> str:
    lines: list[str] = []
    lines.append(f"# Investment Committee Report: {parts.project_name}")
    lines.append("")
    lines.append(f"**Date:** {parts.report_date}")
    lines.append("")

    if parts.has_chair:
        lines.append("---")
        lines.append("")
        lines.append(f"## DECISION: {parts.decision}")
        lines.append("")
        if parts.vetoed:
            lines.append("**RISK OFFICER VETO — the committee cannot override this.**")
            lines.append("")
            if parts.veto_reason:
                lines.append(f"> {parts.veto_reason}")
                lines.append("")
        lines.append(f"- **Conviction:** {parts.conviction}")
        lines.append(f"- **Position Size:** {parts.sizing}")
        lines.append(f"- **Entry Strategy:** {parts.entry}")
        lines.append(f"- **Review Date:** {parts.review}")
        lines.append("")
        lines.append(f"**Chair's Reasoning:** {parts.chair_reasoning}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Score Breakdown")
    lines.append("")
    lines.append(f"**Overall Score: {parts.overall_score}**")
    lines.append("")
    if isinstance(parts.scores, dict) and parts.scores:
        lines.append("| Domain | Score |")
        lines.append("|--------|-------|")
        for domain, score in parts.scores.items():
            lines.append(f"| {str(domain).replace('_', ' ').title()} | {score} |")
        lines.append("")

    for _key, title, content in parts.sections:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(str(content))
        lines.append("")

    if parts.risks:
        lines.append("## Key Risks")
        lines.append("")
        items = parts.risks if isinstance(parts.risks, list) else [parts.risks]
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. {str(r).lstrip('0123456789. ')}")
        lines.append("")

    if parts.opportunities:
        lines.append("## Key Opportunities")
        lines.append("")
        items = parts.opportunities if isinstance(parts.opportunities, list) else [parts.opportunities]
        for i, o in enumerate(items, 1):
            lines.append(f"{i}. {str(o).lstrip('0123456789. ')}")
        lines.append("")

    if parts.mandate:
        lines.append("## Mandate Compliance")
        lines.append("")
        lines.append(str(parts.mandate))
        lines.append("")

    if parts.ray:
        lines.append("---")
        lines.append("")
        lines.append("## Ray's Independent Review")
        lines.append("")
        lines.append(f"**Verdict:** {parts.ray['verdict']}")
        lines.append("")
        lines.append(f"**Summary:** {parts.ray['summary']}")
        lines.append("")
        for label, val in parts.ray["fields"]:
            lines.append(f"**{label}:** {val}")
            lines.append("")

    if parts.signposts:
        lines.append("---")
        lines.append("")
        lines.append("## Signposts to Monitor")
        lines.append("")
        for s in parts.signposts:
            lines.append(f"- {s}")
        lines.append("")

    if parts.conflicts:
        lines.append("## Conflicts Resolved")
        lines.append("")
        for c in parts.conflicts:
            lines.append(f"- {c}")
        lines.append("")

    if parts.footnotes:
        lines.append("---")
        lines.append("")
        lines.append("## Footnotes")
        lines.append("")
        for footnote in parts.footnotes:
            support = f" — {footnote['supports']}" if footnote.get("supports") else ""
            lines.append(f"[{footnote['id']}] [{footnote['label']}]({footnote['url']}){support}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by Committee Orchestrator*")

    return "\n".join(lines)


def _build_markdown_report(project_name: str, agent_outputs: list[dict]) -> str:
    """Build the markdown report from agent outputs."""
    return _render_markdown(_extract_report_parts(project_name, agent_outputs))


# --------------------------------------------------------------------------
# HTML rendering primitives — escape-first, no exceptions
# --------------------------------------------------------------------------

_ALLOWED_SCHEMES = ("http://", "https://", "mailto:")

# Applied to text that has ALREADY been html.escape()d. Alternation order
# matters: code before everything (so its body is left alone), link before
# citation (so "[x](url)" is not mistaken for a footnote marker).
_INLINE_RE = re.compile(
    r"(?P<code>`(?P<code_body>[^`\n]{1,400})`)"
    r"|(?P<link>\[(?P<link_text>[^\]\[\n]{1,400})\]\((?P<link_url>[^()\s]{1,2000})\))"
    r"|(?P<cite>\[(?P<cite_id>\d{1,4})\])"
    r"|(?P<bold>\*\*(?P<bold_body>[^*\n]{1,600})\*\*)"
    r"|(?P<em>(?<![*\w])\*(?P<em_body>[^*\n]{1,400})\*(?![*\w]))"
)

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s{0,3}(\d{1,3})[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|_{3,}|\*{3,})\s*$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def _esc(value: Any) -> str:
    """The single choke point. Nothing reaches the page without passing here."""
    return html.escape("" if value is None else str(value), quote=True)


def _safe_href(escaped_url: str) -> str | None:
    """Accept only http/https/mailto. Input is already html-escaped."""
    candidate = escaped_url.strip()
    if candidate.lower().startswith(_ALLOWED_SCHEMES):
        return candidate
    return None


def _slug(value: str) -> str:
    """Anchor ids come from OUR section keys, never from model output."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return cleaned or "section"


def _inline(raw: Any, fn_ids: frozenset[int] = frozenset(), _depth: int = 0) -> str:
    """Render inline markdown from untrusted text.

    Escapes first, then walks the escaped string once, emitting markup only
    from this function's own literals. Untrusted bytes are never re-inserted
    unescaped, so no input can produce a tag.
    """
    escaped = _esc(raw)
    return _inline_escaped(escaped, fn_ids, _depth)


def _inline_escaped(escaped: str, fn_ids: frozenset[int], depth: int = 0) -> str:
    out: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(escaped):
        out.append(escaped[pos : m.start()])
        pos = m.end()
        if m.group("code"):
            out.append(f'<code>{m.group("code_body")}</code>')
        elif m.group("link"):
            href = _safe_href(m.group("link_url"))
            text = m.group("link_text")
            if href:
                out.append(
                    f'<a href="{href}" rel="noopener noreferrer nofollow ugc" '
                    f'target="_blank">{text}</a>'
                )
            else:
                # Unsupported scheme: show it, do not link it.
                out.append(f'{text} <span class="badurl">({m.group("link_url")})</span>')
        elif m.group("cite"):
            cid = int(m.group("cite_id"))
            if cid in fn_ids:
                out.append(
                    f'<sup class="cite"><a href="#fn-{cid}" '
                    f'aria-label="Jump to footnote {cid}">{cid}</a></sup>'
                )
            else:
                out.append(f'<sup class="cite cite-dead">{cid}</sup>')
        elif m.group("bold"):
            body = m.group("bold_body")
            inner = _inline_escaped(body, fn_ids, depth + 1) if depth < 2 else body
            out.append(f"<strong>{inner}</strong>")
        elif m.group("em"):
            body = m.group("em_body")
            inner = _inline_escaped(body, fn_ids, depth + 1) if depth < 2 else body
            out.append(f"<em>{inner}</em>")
    out.append(escaped[pos:])
    return "".join(out)


def _render_table(rows: list[str], fn_ids: frozenset[int], caption: str = "") -> str:
    """Render a pipe table into its own horizontal scroll container."""

    def cells(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [c.strip() for c in stripped.split("|")]

    body_rows = [r for r in rows if not _TABLE_SEP_RE.match(r)]
    if not body_rows:
        return ""
    header, *rest = body_rows
    parts = ['<div class="table-wrap" role="region" tabindex="0" aria-label="'
             + (_esc(caption) or "Table") + '"><table><thead><tr>']
    parts += [f"<th scope=\"col\">{_inline(c, fn_ids)}</th>" for c in cells(header)]
    parts.append("</tr></thead><tbody>")
    for row in rest:
        parts.append("<tr>")
        parts += [f"<td>{_inline(c, fn_ids)}</td>" for c in cells(row)]
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _render_prose(raw: Any, fn_ids: frozenset[int], heading_level: int = 3) -> str:
    """Render a block of untrusted model prose.

    Handles the small markdown subset models actually emit: paragraphs, lists,
    pipe tables, blockquotes, rules and headings. Headings inside a section
    body are demoted to `heading_level` (h3 directly under a section's h2, h4
    when already nested) so model output cannot forge a top-level heading or
    skip a level and corrupt the document outline.
    """
    hl = min(max(int(heading_level), 3), 6)
    text = "" if raw is None else str(raw)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    out: list[str] = []
    para: list[str] = []
    buf: list[str] = []
    mode: str | None = None

    def flush_para() -> None:
        if para:
            out.append(f'<p>{_inline(" ".join(para), fn_ids)}</p>')
            para.clear()

    def flush_block() -> None:
        nonlocal mode
        if not buf:
            mode = None
            return
        if mode == "ul":
            out.append("<ul>" + "".join(f"<li>{_inline(i, fn_ids)}</li>" for i in buf) + "</ul>")
        elif mode == "ol":
            out.append("<ol>" + "".join(f"<li>{_inline(i, fn_ids)}</li>" for i in buf) + "</ol>")
        elif mode == "table":
            out.append(_render_table(buf, fn_ids))
        elif mode == "quote":
            out.append(f'<blockquote><p>{_inline(" ".join(buf), fn_ids)}</p></blockquote>')
        buf.clear()
        mode = None

    def flush_all() -> None:
        flush_para()
        flush_block()

    for line in lines:
        if not line.strip():
            flush_all()
            continue

        if line.lstrip().startswith("|"):
            if mode != "table":
                flush_all()
                mode = "table"
            buf.append(line)
            continue

        m_ul = _UL_RE.match(line)
        if m_ul:
            if mode != "ul":
                flush_all()
                mode = "ul"
            buf.append(m_ul.group(1))
            continue

        m_ol = _OL_RE.match(line)
        if m_ol:
            if mode != "ol":
                flush_all()
                mode = "ol"
            buf.append(m_ol.group(2))
            continue

        m_q = _QUOTE_RE.match(line)
        if m_q:
            if mode != "quote":
                flush_all()
                mode = "quote"
            buf.append(m_q.group(1))
            continue

        if _HR_RE.match(line):
            flush_all()
            out.append("<hr>")
            continue

        m_h = _HEADING_RE.match(line)
        if m_h:
            flush_all()
            out.append(f"<h{hl}>{_inline(m_h.group(2), fn_ids)}</h{hl}>")
            continue

        flush_block()
        para.append(line.strip())

    flush_all()
    return "".join(out) or f'<p class="muted">{_esc("No content provided.")}</p>'


def _render_value(
    value: Any, fn_ids: frozenset[int], caption: str = "", heading_level: int = 3
) -> str:
    """Render a section body that may be prose, a list, or a mapping."""
    if isinstance(value, dict):
        rows = "".join(
            f"<tr><th scope=\"row\">{_inline(str(k).replace('_', ' ').title(), fn_ids)}</th>"
            f"<td>{_render_value(v, fn_ids, heading_level=heading_level)}</td></tr>"
            for k, v in value.items()
        )
        return (
            f'<div class="table-wrap" role="region" tabindex="0" '
            f'aria-label="{_esc(caption) or "Details"}"><table>{rows}</table></div>'
        )
    if isinstance(value, list):
        if not value:
            return ""
        return (
            "<ul>"
            + "".join(
                f"<li>{_render_value(v, fn_ids, heading_level=heading_level)}</li>"
                for v in value
            )
            + "</ul>"
        )
    if isinstance(value, str):
        return _render_prose(value, fn_ids, heading_level)
    return _inline(value, fn_ids)


def _as_number(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _human_time(value: Any) -> str:
    """ISO timestamp -> "2026-08-24 08:56 UTC". Falls back to the raw string."""
    if not value:
        return ""
    text = str(value)
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text


# --------------------------------------------------------------------------
# Page assembly
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# Styling for the download toolbar, kept here rather than in tpl.html because
# the toolbar is emitted from this module and nothing else uses it. Reuses the
# template's `.btn`, which the print stylesheet already hides; `.actions` is
# hidden too so the row leaves no gap on a printed page or a saved PDF.
REPORT_HEAD_CSS = (
    "<style>"
    ".actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
    "margin:18px 0 0}"
    ".actions .btn{margin-top:0}"
    ".actions .hint{font-size:.82rem;color:var(--muted)}"
    "@media print{.actions{display:none !important}}"
    "</style>"
)


def _page(title: str, body: str, extra_head: str = "") -> str:
    """Fill the shell template.

    Substitution is a single regex pass over the template driven by a dict, so
    a token appearing inside a substituted value is never re-substituted.
    `title` is raw text and is escaped here — the one place it happens.
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    values = {"TITLE": _esc(title), "BODY": body, "HEAD": extra_head}
    return _TOKEN_RE.sub(lambda m: values.get(m.group(1), ""), template)


def _html_response(
    title: str,
    body: str,
    status: int = 200,
    extra_head: str = "",
    extra_headers: dict[str, str] | None = None,
) -> HTMLResponse:
    headers = dict(SECURITY_HEADERS)
    headers.update(extra_headers or {})
    return HTMLResponse(
        content=_page(title, body, extra_head),
        status_code=status,
        headers=headers,
    )


def _state_page(
    *,
    status_code: int,
    heading: str,
    detail: str,
    note: str = "",
    kind: str = "info",
) -> HTMLResponse:
    """A styled page for every non-report state.

    CONTRACTS §3.4 fixes the *message* (`detail`); it does not require a JSON
    media type on a browser-facing HTML endpoint. The same `detail` string the
    JSON endpoints return is rendered here and also emitted as a
    machine-readable <meta> so a scripted client can still read it.
    """
    note_html = f'<p class="muted">{_esc(note)}</p>' if note else ""
    body = (
        '<main id="main" class="state">'
        f'<div class="state-card state-{_esc(kind)}">'
        f'<p class="state-code">{status_code}</p>'
        f"<h1>{_esc(heading)}</h1>"
        f'<p class="state-detail">{_esc(detail)}</p>'
        f"{note_html}"
        f'<p><a class="btn" href="/api/reports/html">All reports</a></p>'
        "</div></main>"
    )
    head = f'<meta name="error-detail" content="{_esc(detail)}">'
    return _html_response(
        f"{heading} · AIIC Committee Report", body, status=status_code, extra_head=head
    )


def _decision_style(decision: str) -> tuple[str, str, str]:
    key = (decision or "").strip().upper().replace(" ", "_")
    return DECISION_STYLES.get(key, ("unknown", "•", key.replace("_", " ").title() or "Undecided"))


def _render_report_html(parts: ReportParts, show_actions: bool = True) -> str:
    fn_ids = frozenset(int(f["id"]) for f in parts.footnotes if str(f.get("id", "")).isdigit())
    toc: list[tuple[str, str]] = []
    lede: list[str] = []   # masthead, banners, decision hero — always above the fold
    body: list[str] = []   # the scrolling sections, paired with the TOC

    def section(anchor: str, title: str, inner: str, cls: str = "") -> None:
        toc.append((anchor, title))
        klass = f' class="{cls}"' if cls else ""
        body.append(
            f'<section id="{anchor}"{klass} aria-labelledby="{anchor}-h">'
            f'<h2 id="{anchor}-h">{_esc(title)}</h2>{inner}</section>'
        )

    # ---- masthead -------------------------------------------------------
    head_bits = [f'<span class="meta-item">{_esc(parts.report_date)}</span>']
    if parts.ticker:
        head_bits.append(f'<span class="meta-item">{_esc(parts.ticker)}</span>')
    if parts.completed_at:
        head_bits.append(
            f'<span class="meta-item">Completed {_esc(parts.completed_at)}</span>'
        )
    if parts.evaluation_id:
        head_bits.append(f'<span class="meta-item mono">{_esc(parts.evaluation_id)}</span>')

    # The download row. `evaluation_id` is a parsed UUID by the time it lands
    # in ReportParts, so the hrefs below cannot carry anything but hex and
    # dashes — no escaping question arises. Omitted when the id is unknown
    # (only reachable from a direct _render_report_html call in a test), and
    # omitted from the downloaded copy: its hrefs are site-relative, so in a
    # file:// copy they would resolve against the local disk and 404. A saved
    # page should not offer to download itself anyway.
    actions_html = ""
    if parts.evaluation_id and show_actions:
        base = f"/api/reports/{_esc(parts.evaluation_id)}"
        actions_html = (
            '<p class="actions">'
            f'<a class="btn" href="{base}/markdown?download=1" '
            'download>Download markdown</a>'
            f'<a class="btn" href="{base}/html?download=1" download>Save this page</a>'
            '<span class="hint">For a PDF, print this page and choose '
            '&ldquo;Save as PDF&rdquo; — it has a print stylesheet.</span>'
            "</p>"
        )

    lede.append(
        '<header class="masthead">'
        '<p class="eyebrow">Investment Committee Report</p>'
        f"<h1>{_esc(parts.project_name)}</h1>"
        f'<p class="meta">{"".join(head_bits)}</p>'
        f"{actions_html}"
        "</header>"
    )

    # ---- degraded-report banners ---------------------------------------
    if parts.agents_missing or parts.agents_errored:
        notes = []
        if parts.agents_missing:
            notes.append(
                "Missing agent output: " + ", ".join(_esc(a) for a in parts.agents_missing) + "."
            )
        if parts.agents_errored:
            notes.append(
                "Agents that errored: " + ", ".join(_esc(a) for a in parts.agents_errored) + "."
            )
        lede.append(
            '<div class="banner banner-warn" role="status">'
            '<span class="banner-glyph" aria-hidden="true">!</span>'
            "<div><strong>Partial report.</strong> "
            + " ".join(notes)
            + " Sections below reflect only what completed.</div></div>"
        )

    # ---- decision hero --------------------------------------------------
    if parts.has_chair:
        cls, glyph, label = _decision_style(parts.decision)
        score_num = _as_number(parts.overall_score)
        score_display = (
            f"{score_num:g}" if score_num is not None else _esc(str(parts.overall_score))
        )

        veto_html = ""
        if parts.vetoed:
            reason = (
                f'<div class="veto-reason">{_render_prose(parts.veto_reason, fn_ids)}</div>'
                if parts.veto_reason
                else ""
            )
            veto_html = (
                '<div class="veto-banner" role="alert">'
                '<span class="banner-glyph" aria-hidden="true">✕</span>'
                "<div><strong>Risk Officer veto.</strong> "
                "The Chair may acknowledge it but cannot override it."
                f"{reason}</div></div>"
            )

        facts = "".join(
            f'<div class="fact"><dt>{_esc(k)}</dt><dd>{_inline(v, fn_ids)}</dd></div>'
            for k, v in (
                ("Conviction", parts.conviction),
                ("Position Size", parts.sizing),
                ("Entry Strategy", parts.entry),
                ("Review Date", parts.review),
            )
        )

        toc.append(("decision", "Decision"))
        lede.append(
            f'<section id="decision" class="hero decision-{cls}" aria-labelledby="decision-h">'
            f'<h2 id="decision-h" class="sr-only">Committee decision</h2>'
            '<div class="hero-top">'
            '<div class="verdict">'
            f'<span class="verdict-glyph" aria-hidden="true">{glyph}</span>'
            f'<span class="verdict-word">{_esc(parts.decision or "Undecided")}</span>'
            f'<span class="sr-only">Committee decision: {_esc(label)}</span>'
            "</div>"
            '<div class="score">'
            f'<span class="score-num">{score_display}</span>'
            '<span class="score-den">/ 100</span>'
            '<span class="score-cap">Overall score</span>'
            "</div>"
            "</div>"
            f"{veto_html}"
            f'<dl class="facts">{facts}</dl>'
            '<div class="chair-reasoning">'
            "<h3>Chair's reasoning</h3>"
            f"{_render_prose(parts.chair_reasoning, fn_ids)}"
            "</div>"
            "</section>"
        )

    # ---- score breakdown ------------------------------------------------
    if isinstance(parts.scores, dict) and parts.scores:
        rows = []
        for domain, score in parts.scores.items():
            num = _as_number(score)
            pct = max(0, min(100, int(num))) if num is not None else 0
            bar = (
                f'<span class="bar" role="img" '
                f'aria-label="{_esc(str(score))} out of 100">'
                f'<span class="bar-fill" style="width:{pct}%"></span></span>'
            )
            rows.append(
                '<li class="score-row">'
                f'<span class="score-label">{_esc(str(domain).replace("_", " ").title())}</span>'
                f"{bar}"
                f'<span class="score-val mono">{_esc(str(score))}</span>'
                "</li>"
            )
        section("score-breakdown", "Score Breakdown", f'<ul class="scores">{"".join(rows)}</ul>')
    elif parts.scores:
        section("score-breakdown", "Score Breakdown", _render_value(parts.scores, fn_ids, "Scores"))

    # ---- the 17 narrative sections --------------------------------------
    for key, title, content in parts.sections:
        section(f"sec-{_slug(key)}", title, _render_value(content, fn_ids, title))

    # ---- ranked lists ---------------------------------------------------
    def ranked(anchor: str, title: str, value: Any, cls: str) -> None:
        if not value:
            return
        items = value if isinstance(value, list) else [value]
        lis = "".join(
            f'<li>{_render_value(str(i).lstrip("0123456789. "), fn_ids)}</li>' for i in items
        )
        section(anchor, title, f'<ol class="ranked {cls}">{lis}</ol>')

    ranked("key-risks", "Key Risks", parts.risks, "ranked-risk")
    ranked("key-opportunities", "Key Opportunities", parts.opportunities, "ranked-opp")

    if parts.mandate:
        section("mandate-compliance", "Mandate Compliance", _render_value(parts.mandate, fn_ids))

    # ---- Ray ------------------------------------------------------------
    if parts.ray:
        inner = [
            '<div class="ray-lede">'
            f'<p><span class="tag">Verdict</span>{_inline(parts.ray["verdict"], fn_ids)}</p>'
            "</div>",
            _render_value(parts.ray["summary"], fn_ids),
        ]
        for label, val in parts.ray["fields"]:
            inner.append(
                f"<h3>{_esc(label)}</h3>"
                + _render_value(val, fn_ids, heading_level=4)
            )
        section("rays-review", "Ray's Independent Review", "".join(inner), cls="ray")

    if parts.signposts:
        items = parts.signposts if isinstance(parts.signposts, list) else [parts.signposts]
        section(
            "signposts",
            "Signposts to Monitor",
            "<ul>" + "".join(f"<li>{_render_value(s, fn_ids)}</li>" for s in items) + "</ul>",
        )

    if parts.conflicts:
        items = parts.conflicts if isinstance(parts.conflicts, list) else [parts.conflicts]
        section(
            "conflicts",
            "Conflicts Resolved",
            "<ul>" + "".join(f"<li>{_render_value(c, fn_ids)}</li>" for c in items) + "</ul>",
        )

    # ---- footnotes ------------------------------------------------------
    if parts.footnotes:
        items = []
        for fn in parts.footnotes:
            fid = _esc(fn.get("id", ""))
            url_escaped = _esc(fn.get("url", ""))
            href = _safe_href(url_escaped)
            label = _esc(fn.get("label") or fn.get("url") or "Source")
            link = (
                f'<a href="{href}" rel="noopener noreferrer nofollow ugc" target="_blank">{label}</a>'
                if href
                else f"{label} <span class=\"badurl\">({url_escaped})</span>"
            )
            kind = fn.get("kind")
            kind_html = f'<span class="fn-kind">{_esc(kind)}</span>' if kind else ""
            supports = fn.get("supports")
            supports_html = f'<span class="fn-supports">{_esc(supports)}</span>' if supports else ""
            host_html = ""
            if href:
                host = _esc(href.split("//", 1)[-1].split("/", 1)[0])
                host_html = '<span class="fn-host">' + host + "</span>"
            items.append(
                f'<li id="fn-{fid}" class="fn">'
                f'<span class="fn-id" aria-hidden="true">{fid}</span>'
                f'<span class="fn-body">{link}{host_html}'
                f"{kind_html}{supports_html}</span></li>"
            )
        section("footnotes", "Footnotes", f'<ol class="footnotes">{"".join(items)}</ol>')

    # ---- table of contents ---------------------------------------------
    toc_html = (
        '<nav class="toc" aria-label="Report sections"><h2 class="toc-title">Contents</h2><ol>'
        + "".join(f'<li><a href="#{a}">{_esc(t)}</a></li>' for a, t in toc)
        + "</ol></nav>"
    )

    skip_target = "#decision" if parts.has_chair else "#main"
    return (
        f'<a class="skip" href="{skip_target}">Skip to report</a>'
        f'{"".join(lede)}'
        '<div class="layout">'
        f"{toc_html}"
        f'<main id="main">{"".join(body)}'
        '<footer class="colophon"><p>Generated by Committee Orchestrator.</p></footer>'
        "</main></div>"
    )


def _render_index_html(rows: list[dict]) -> str:
    if not rows:
        inner = (
            '<div class="state-card state-empty">'
            "<h2>No reports yet</h2>"
            '<p class="muted">No evaluation has completed. Start one with '
            "<code>POST /api/evaluate</code> or the Telegram bot, then come back.</p>"
            "</div>"
        )
    else:
        items = []
        for r in rows:
            cls, glyph, label = _decision_style(r.get("decision", ""))
            when = _esc(_human_time(r.get("completed_at")))
            eid = _esc(r.get("evaluation_id", ""))
            name = _esc(r.get("project_name", "Unknown"))
            ticker_raw = r.get("ticker")
            ticker = f'<span class="meta-item">{_esc(ticker_raw)}</span>' if ticker_raw else ""
            items.append(
                f'<li class="index-row decision-{cls}">'
                f'<a href="/api/reports/{eid}/html">'
                f'<span class="index-glyph" aria-hidden="true">{glyph}</span>'
                f'<span class="index-name">{name}</span>'
                f'<span class="sr-only">, decision {_esc(label)}</span>'
                f'<span class="meta">{ticker}<span class="meta-item">{when}</span></span>'
                "</a></li>"
            )
        inner = f'<ol class="index-list">{"".join(items)}</ol>'

    return (
        '<a class="skip" href="#main">Skip to list</a>'
        '<header class="masthead">'
        '<p class="eyebrow">Committee Orchestrator</p>'
        "<h1>Reports</h1>"
        f'<p class="meta"><span class="meta-item">{len(rows)} completed</span></p>'
        "</header>"
        f'<main id="main" class="index">{inner}</main>'
    )


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------


class ReportUnavailable(Exception):
    """Raised when a report cannot be produced. Carries the §3.4 detail."""

    def __init__(self, status_code: int, detail: str, kind: str = "error", note: str = ""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.kind = kind
        self.note = note


async def _load_report_parts(evaluation_id: UUID, db: AsyncSession) -> ReportParts:
    """Load an evaluation and rebuild its report document.

    FOLLOW-UP (agent/persistence): once `Report` rows are written, this is the
    one place to change — read the stored `Report` for `evaluation_id` and, if
    absent, fall back to the agent_outputs rebuild below. Nothing else in this
    module touches the database.
    """
    result = await db.execute(select(Evaluation).where(Evaluation.id == evaluation_id))
    evaluation = result.scalar_one_or_none()
    if not evaluation:
        raise ReportUnavailable(404, "Evaluation not found", kind="missing")

    status = (evaluation.status or "unknown").lower()
    if status in {"pending", "queued", "running", "in_progress"}:
        raise ReportUnavailable(
            202,
            f"Evaluation status is {evaluation.status}",
            kind="running",
            note="The committee is still deliberating. Reload in a few minutes.",
        )
    if status != "completed":
        raise ReportUnavailable(
            409,
            f"Evaluation status is {evaluation.status}",
            kind="failed",
            note="This evaluation did not complete, so no report was produced. "
            "Check the server logs for the cause.",
        )

    proj_result = await db.execute(select(Project).where(Project.id == evaluation.project_id))
    project = proj_result.scalar_one_or_none()

    outputs_result = await db.execute(
        select(AgentOutput).where(AgentOutput.evaluation_id == evaluation.id)
    )
    outputs = outputs_result.scalars().all()

    agent_outputs = [
        {
            "agent_name": o.agent_name,
            "output": o.output,
            "score": float(o.score) if o.score else None,
            "error": o.error,
        }
        for o in outputs
    ]

    parts = _extract_report_parts(project.name if project else "Unknown", agent_outputs)
    parts.evaluation_id = str(evaluation.id)
    parts.ticker = (project.ticker or "") if project else ""
    if evaluation.completed_at:
        parts.completed_at = evaluation.completed_at.strftime("%Y-%m-%d %H:%M UTC")
    return parts


# --------------------------------------------------------------------------
# Download: Content-Disposition and the filename that goes in it
# --------------------------------------------------------------------------

# Everything a filename may contain. Deliberately NARROWER than the
# [A-Za-z0-9._-] the brief allows: only alphanumerics survive from untrusted
# input and the separators are supplied by this module, so no input can produce
# `.`, `..`, a leading dash, or a path separator no matter what it contains.
_FILENAME_KEEP_RE = re.compile(r"[^A-Za-z0-9]+")

# Long enough to stay recognisable, short enough that project name + date + the
# `aiic-` prefix + extension stays well inside every filesystem's 255-byte limit.
_SLUG_MAX = 60


def _filename_slug(value: Any, fallback: str) -> str:
    """Collapse arbitrary text to a lowercase `[a-z0-9-]` slug.

    The inputs are hostile by provenance: `project_name` starts as a Telegram
    message and `report_date` is whatever the Report Writer LLM emitted. A
    header value is a single line by definition, so anything that could split
    it — CR, LF, `"`, `;` — must not survive, and it does not: the regex keeps
    alphanumerics only and replaces every other run with a single `-`.
    """
    slug = _FILENAME_KEEP_RE.sub("-", str(value or "")).strip("-").lower()
    slug = slug[:_SLUG_MAX].strip("-")
    return slug or fallback


def _download_filename(parts: ReportParts, extension: str) -> str:
    """`aiic-aave-2026-06-11.md` — safe by construction, never by filtering."""
    name = _filename_slug(parts.project_name, "report")
    date = _filename_slug(parts.report_date, "")
    stem = f"aiic-{name}-{date}" if date else f"aiic-{name}"
    return f"{stem}.{extension}"


def _attachment_headers(parts: ReportParts, extension: str) -> dict[str, str]:
    """Content-Disposition for a download, plus the headers it needs.

    `nosniff` matters here specifically: a saved report is LLM-authored text
    from the open internet, and a browser that sniffs a `.md` attachment as
    HTML would undo the whole escape-first design of the HTML renderer.
    """
    filename = _download_filename(parts, extension)
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/html", response_class=HTMLResponse)
async def get_index_html(db: AsyncSession = Depends(get_db)):
    """Human-facing index of completed reports, including the empty state."""
    rows = await _list_report_rows(db)
    return _html_response("Reports · AIIC Committee", _render_index_html(rows))


@router.get("/{evaluation_id}/markdown", response_class=PlainTextResponse)
async def get_markdown_report(
    evaluation_id: UUID,
    db: AsyncSession = Depends(get_db),
    download: bool = Query(
        False, description="Serve as a file attachment instead of inline text."
    ),
):
    """Get a formatted markdown report for an evaluation.

    `?download=1` returns the same bytes with a `Content-Disposition:
    attachment` header so the browser (or `curl -OJ`) saves a file. The inline
    response is byte-for-byte unchanged, headers included — a link that worked
    before still behaves exactly as it did.
    """
    try:
        parts = await _load_report_parts(evaluation_id, db)
    except ReportUnavailable as exc:
        # Preserve the historical JSON status codes for scripted clients.
        status = 404 if exc.status_code == 404 else 400
        raise HTTPException(status_code=status, detail=exc.detail) from None
    markdown = _render_markdown(parts)
    if not download:
        return markdown
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers=_attachment_headers(parts, "md"),
    )


@router.get("/{evaluation_id}/html", response_class=HTMLResponse)
async def get_html(
    evaluation_id: str,
    db: AsyncSession = Depends(get_db),
    download: bool = Query(
        False, description="Serve as a file attachment instead of rendering inline."
    ),
):
    """Server-rendered HTML report. No JavaScript, no external requests.

    `evaluation_id` is taken as `str` rather than `UUID` on purpose: a report
    link that got truncated in Telegram should land on the styled 404 page a
    human can act on, not FastAPI's raw JSON 422.

    `?download=1` saves the page as a self-contained `.html` file. The CSS is
    already inline and there are no subresources, so the saved file renders
    offline exactly as it does here. Error and in-progress states are always
    served inline — there is nothing worth saving in them.
    """
    try:
        parsed = UUID(evaluation_id)
    except (ValueError, AttributeError, TypeError):
        return _state_page(
            status_code=404,
            heading="Report not found",
            detail="Evaluation not found",
            note="That is not a valid evaluation id. The link may have been "
            "truncated in transit.",
            kind="missing",
        )
    try:
        parts = await _load_report_parts(parsed, db)
    except ReportUnavailable as exc:
        headings = {
            "missing": "Report not found",
            "running": "Evaluation in progress",
            "failed": "Report unavailable",
        }
        return _state_page(
            status_code=exc.status_code,
            heading=headings.get(exc.kind, "Report unavailable"),
            detail=exc.detail,
            note=exc.note,
            kind=exc.kind,
        )
    except Exception:
        logger.exception("Failed to render HTML report for %s", evaluation_id)
        return _state_page(
            status_code=500,
            heading="Report could not be rendered",
            detail="Internal server error",
            note="The evaluation exists but its output could not be rendered. "
            "The failure has been logged.",
            kind="failed",
        )

    _cls, _glyph, label = _decision_style(parts.decision)
    title = f"{parts.project_name} — {label} · AIIC Committee Report"
    return _html_response(
        title,
        _render_report_html(parts, show_actions=not download),
        extra_head=REPORT_HEAD_CSS,
        extra_headers=_attachment_headers(parts, "html") if download else None,
    )


async def _list_report_rows(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Evaluation, Project)
        .join(Project, Evaluation.project_id == Project.id)
        .where(Evaluation.status == "completed")
        .order_by(Evaluation.completed_at.desc())
    )
    rows = result.all()

    decisions: dict[str, str] = {}
    if rows:
        chair_result = await db.execute(
            select(AgentOutput).where(
                AgentOutput.agent_name == "committee_chair",
                AgentOutput.evaluation_id.in_([e.id for e, _ in rows]),
            )
        )
        for ao in chair_result.scalars().all():
            output = ao.output if isinstance(ao.output, dict) else {}
            decisions[str(ao.evaluation_id)] = str(output.get("decision", ""))

    return [
        {
            "evaluation_id": str(e.id),
            "project_name": p.name,
            "ticker": p.ticker,
            "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            "decision": decisions.get(str(e.id), ""),
        }
        for e, p in rows
    ]


@router.get("")
async def list_reports(db: AsyncSession = Depends(get_db)):
    """List all completed evaluations."""
    return {"reports": await _list_report_rows(db)}
