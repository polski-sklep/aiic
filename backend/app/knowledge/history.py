"""Prior-evaluation retrieval: what did this committee say about this project last time?

Today a re-evaluation of a project produces a standalone report with no
reference to the previous one. The Chair names signposts, sets a review date,
and nothing ever revisits them (`AIIC_HANDOFF.md` §6.5). The raw material is all
persisted and all unused:

* ``reports``            — the full prior evaluation result as JSONB, versioned.
                           Written only since 2026-08-25; earlier evaluations
                           have no row here at all (`docs/CONTRACTS.md` §2.3 was
                           written when the table had zero rows).
* ``agent_outputs``      — per-agent prior analysis, present for 15 evaluations
                           going back to April 2026. This is the fallback path
                           and the only path for anything before today.
* ``calibration_records``— the entry price, the 30/90/180-day marks, the return
                           and the alpha against BTC. Plus, since 2026-08-25,
                           the Chair's own ``signposts`` and ``review_date``.

This module joins those three and answers one question: *what is the most recent
completed prior evaluation of this project, and how did it turn out?*

--- Choice of project key -------------------------------------------------

``coingecko_id`` is the primary key, with a case-insensitive ``projects.name``
match as the fallback. Reasons, in order of weight:

1. Project names are free text lifted from a Telegram message. "Chainlink",
   "chainlink" and "LINK" are the same asset and three different strings.
   ``api/evaluate.py`` finds-or-creates a ``projects`` row with
   ``Project.name == req.project_name`` — an *exact, case-sensitive* match — so
   a differently-cased name silently forks a second project row and every prior
   evaluation becomes invisible. Keying on the coingecko id defeats that.
2. The coingecko id is the identifier the price side of the system already runs
   on: ``calibration_records.coingecko_id`` is what ``update_checkpoint`` uses
   to fetch the historical price, so it is the only key that reaches both the
   reasoning and the outcome.

But it is not sufficient on its own, and the live ledger proves it: the Plasma
calibration rows carry ``coingecko_id = 'plasma'`` while the Plasma ``projects``
row carries ``'plasma-xpl'``. The 18 June cohort was written by a harness that
lower-cased the project name and called it an id (`docs/CONTRACTS.md` §2.6). So
the id drifts, and it is also simply absent whenever a caller omits it. Hence:
exact id match first; case-insensitive name match when the id yields nothing.
The union is taken deliberately — the two keys fail in different directions.

--- What "no prior" means -------------------------------------------------

Four real states, each with an answer rather than an exception:

* No ``projects`` row, or none of its evaluations is usable   -> ``None``.
* An evaluation that is still ``running``                     -> ignored; a run
  in flight is not a prior. (It is usually *this* run.)
* An evaluation whose row is ``failed``, or whose Chair errored, or whose
  decision came back ``CHAIR_FAILED``                         -> skipped, and
  its id recorded in ``skipped_unusable`` so the skip is visible. If every
  candidate is like that, the newest one is returned with ``usable=False`` and
  a plain-English ``unusable_reason``, so the report can say so in one line
  instead of pretending there was never a prior at all.
* An evaluation with ``agent_outputs`` but no ``reports`` row (the thirteen
  historical ones) -> served from ``agent_outputs``; ``source`` says which.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: How far back to walk when every recent evaluation turns out to be unusable.
#: Chainlink's 1 June pair is the shape this exists for: a run that 429'd on
#: every agent, followed fourteen minutes later by the real one.
MAX_CANDIDATES = 10

#: A calibration row written before ``evaluation_id`` was passed (all eight
#: pre-2026-08-25 rows have it NULL) is matched back to its evaluation by time.
#: ``record_calibration`` runs at the very end of ``Orchestrator.evaluate``, so
#: the row lands within seconds of ``evaluations.completed_at`` — the observed
#: gap on the Aave pair is 10 ms and 90 ms. A day is three orders of magnitude
#: of slack and still cannot reach the next evaluation of the same project.
CALIBRATION_MATCH_WINDOW_SECONDS = 86400

#: Hard ceiling on the rendered prior-context block, in characters.
#:
#: The prior report itself is ~43,000 characters and there is no version of
#: "paste the last report in" that is worth its input cost — especially with a
#: parallel workstream cutting input cost at the same time. What actually
#: answers "has anything changed" is the decision, the score, the named
#: signposts, the review date and the price outcome: a few hundred tokens.
PRIOR_CONTEXT_CHAR_LIMIT = 3000

_SIGNPOST_LIMIT = 8
_SIGNPOST_CHARS = 220
_RISK_LIMIT = 5
_RISK_CHARS = 300
_SUMMARY_CHARS = 700

#: Some report_writer runs emit ``18_key_risks`` as one numbered string instead
#: of the five-element array the contract asks for (the Aave 11 June run does).
#: Clipping that single string at the per-item cap would silently discard risks
#: two through five, so a lone string gets a proportionally larger allowance.
_SOLO_STRING_MULTIPLIER = 3

#: Decisions that are not a verdict. ``CHAIR_FAILED`` is written by the
#: orchestrator when adjudication produced no usable JSON; it is deliberately
#: not gradeable and it is deliberately not a prior to compare against.
_NON_VERDICTS = frozenset({"", "CHAIR_FAILED", "FAIL_GATE"})


def _clip(text: Any, limit: int) -> str:
    """One field, bounded, cut on a word boundary where one is near the cap."""
    value = "" if text is None else str(text).strip()
    if len(value) <= limit:
        return value
    head = value[:limit]
    space = head.rfind(" ")
    if space > limit * 3 // 4:
        head = head[:space]
    return head.rstrip(" ,;:.") + "…"


def _strings(value: Any, *, limit: int, chars: int) -> list[str]:
    """A JSON value that should be a list of strings, defensively."""
    if isinstance(value, str):
        return [_clip(value, chars * _SOLO_STRING_MULTIPLIER)] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _clip(item if isinstance(item, str) else json.dumps(item, default=str), chars)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value:+.1f}%"


@dataclass(frozen=True)
class PriorOutcome:
    """The calibration ledger's view of a prior decision: what the price did."""

    record_id: str
    recommendation: str
    entry_price_usd: float | None
    entry_captured_at: datetime | None
    btc_price_at_entry: float | None
    marks: dict[int, dict[str, float | None]] = field(default_factory=dict)
    signposts: list[str] = field(default_factory=list)
    review_date: date | None = None
    linked_by: str = "evaluation_id"

    @property
    def graded_horizons(self) -> list[int]:
        return sorted(h for h, m in self.marks.items() if m.get("return_pct") is not None)


@dataclass(frozen=True)
class PriorEvaluation:
    """The most recent prior evaluation of one project, and how it turned out."""

    evaluation_id: str
    project_id: str
    project_name: str
    coingecko_id: str | None
    matched_by: str                     # 'coingecko_id' | 'project_name'
    evaluated_at: datetime | None
    days_since: int | None
    status: str                         # evaluations.status
    source: str                         # 'reports' | 'agent_outputs' | 'none'
    usable: bool
    unusable_reason: str | None = None

    decision: str | None = None
    conviction: str | None = None
    overall_score: float | None = None
    report_recommendation: str | None = None
    chair_summary: str = ""
    executive_summary: str = ""
    signposts: list[str] = field(default_factory=list)
    signposts_source: str = "none"      # 'calibration' | 'chair' | 'report' | 'none'
    review_date: date | None = None
    key_risks: list[str] = field(default_factory=list)
    outcome: PriorOutcome | None = None
    report_version: int | None = None
    skipped_unusable: list[str] = field(default_factory=list)

    @property
    def review_date_passed(self) -> bool | None:
        if self.review_date is None:
            return None
        return self.review_date <= datetime.now(timezone.utc).date()


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


async def _resolve_project(session: Any, project_name: str, coingecko_id: str | None):
    """Candidate ``projects`` rows, and which key found them. See module docstring."""
    from sqlalchemy import text as sql_text

    cg = (coingecko_id or "").strip().lower()
    if cg:
        rows = (
            await session.execute(
                sql_text(
                    "SELECT id, name, coingecko_id FROM projects "
                    "WHERE lower(coalesce(coingecko_id, '')) = :cg"
                ),
                {"cg": cg},
            )
        ).fetchall()
        if rows:
            return list(rows), "coingecko_id"

    name = (project_name or "").strip().lower()
    if not name:
        return [], "none"
    rows = (
        await session.execute(
            sql_text("SELECT id, name, coingecko_id FROM projects WHERE lower(name) = :name"),
            {"name": name},
        )
    ).fetchall()
    return list(rows), "project_name"


async def _load_report_row(session: Any, evaluation_id: uuid.UUID) -> dict[str, Any] | None:
    """The persisted ``reports`` row for one evaluation, projected, not dumped.

    The live Hyperliquid row is 496,251 characters of JSONB. Selecting the whole
    thing to read six fields out of it would pull half a megabyte across the
    wire per evaluation, so the projection happens in Postgres.
    """
    from sqlalchemy import text as sql_text

    row = (
        await session.execute(
            sql_text(
                """
                SELECT version,
                       recommendation,
                       overall_score,
                       content->'draft_report'->'sections'->>'1_executive_summary' AS exec_summary,
                       content->'draft_report'->'sections'->'18_key_risks'         AS key_risks,
                       content->'draft_report'->'sections'->'24_signposts_to_monitor' AS report_signposts,
                       content->'signposts'                                         AS signposts,
                       content->>'review_date'                                      AS review_date,
                       content->>'chair_reasoning'                                  AS chair_reasoning
                  FROM reports
                 WHERE evaluation_id = :eid
                 ORDER BY version DESC
                 LIMIT 1
                """
            ),
            {"eid": evaluation_id},
        )
    ).fetchone()
    if row is None:
        return None
    return {
        "version": row.version,
        "recommendation": row.recommendation,
        "overall_score": _number(row.overall_score),
        "exec_summary": row.exec_summary,
        "key_risks": row.key_risks,
        "report_signposts": row.report_signposts,
        "signposts": row.signposts,
        "review_date": row.review_date,
        "chair_reasoning": row.chair_reasoning,
    }


async def _load_agent_outputs(session: Any, evaluation_id: uuid.UUID) -> dict[str, Any]:
    """Chair verdict plus the Report Writer's headline sections, projected.

    This is the only path for the thirteen evaluations that predate the
    ``reports`` table being written to (`docs/CONTRACTS.md` §2.3).
    """
    from sqlalchemy import text as sql_text

    chair = (
        await session.execute(
            sql_text(
                "SELECT output, error FROM agent_outputs "
                "WHERE evaluation_id = :eid AND agent_name = 'committee_chair' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"eid": evaluation_id},
        )
    ).fetchone()

    writer = (
        await session.execute(
            sql_text(
                """
                SELECT output->'sections'->>'1_executive_summary'      AS exec_summary,
                       output->'sections'->'18_key_risks'              AS key_risks,
                       output->'sections'->'24_signposts_to_monitor'   AS report_signposts,
                       output->'sections'->>'23_recommendation'        AS recommendation,
                       output->'sections'->>'22_overall_score'         AS overall_score
                  FROM agent_outputs
                 WHERE evaluation_id = :eid AND agent_name = 'report_writer'
                 ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"eid": evaluation_id},
        )
    ).fetchone()

    chair_output = chair.output if chair is not None and isinstance(chair.output, dict) else {}
    return {
        "chair": chair_output,
        "chair_error": (chair.error if chair is not None else None),
        "exec_summary": writer.exec_summary if writer is not None else None,
        "key_risks": writer.key_risks if writer is not None else None,
        "report_signposts": writer.report_signposts if writer is not None else None,
        "recommendation": writer.recommendation if writer is not None else None,
        "overall_score": _number(writer.overall_score) if writer is not None else None,
        "has_any": chair is not None or writer is not None,
    }


async def _load_outcome(
    session: Any,
    evaluation_id: uuid.UUID,
    project_name: str,
    coingecko_id: str | None,
    anchor: datetime | None,
    decision: str | None,
) -> PriorOutcome | None:
    """The calibration record for a prior evaluation.

    Preferred join is the foreign key. All eight rows written before
    ``evaluation_id`` was threaded through carry NULL there
    (`docs/CONTRACTS.md` §2.4), so the fallback matches on project identity and
    proximity to ``completed_at``, preferring a row whose recommendation agrees
    with the decision we already read. That last tiebreak matters: the Aave pair
    on 11 June wrote two rows nineteen minutes apart, INSUFFICIENT_DATA from the
    run that 429'd and PASS from the real one.
    """
    from sqlalchemy import text as sql_text

    columns = """
        id, recommendation, entry_price_usd, entry_captured_at, btc_price_at_entry,
        price_30d, price_90d, price_180d,
        return_30d_pct, return_90d_pct, return_180d_pct,
        alpha_vs_btc_30d_pct, alpha_vs_btc_90d_pct, alpha_vs_btc_180d_pct,
        signposts, review_date
    """

    row = (
        await session.execute(
            sql_text(
                f"SELECT {columns} FROM calibration_records "
                "WHERE evaluation_id = :eid ORDER BY created_at DESC LIMIT 1"
            ),
            {"eid": evaluation_id},
        )
    ).fetchone()
    linked_by = "evaluation_id"

    if row is None and anchor is not None:
        row = (
            await session.execute(
                sql_text(
                    f"""
                    SELECT {columns} FROM calibration_records
                     WHERE evaluation_id IS NULL
                       AND (lower(project_name) = :name OR lower(coalesce(coingecko_id,'')) = :cg)
                       AND abs(extract(epoch FROM (created_at - :anchor))) <= :window
                     ORDER BY (recommendation IS DISTINCT FROM :decision),
                              abs(extract(epoch FROM (created_at - :anchor)))
                     LIMIT 1
                    """
                ),
                {
                    "name": (project_name or "").strip().lower(),
                    "cg": (coingecko_id or "").strip().lower(),
                    "anchor": anchor,
                    "window": CALIBRATION_MATCH_WINDOW_SECONDS,
                    "decision": decision,
                },
            )
        ).fetchone()
        linked_by = "project_name+time"

    if row is None:
        return None

    marks: dict[int, dict[str, float | None]] = {}
    for horizon in (30, 90, 180):
        marks[horizon] = {
            "price": _number(getattr(row, f"price_{horizon}d")),
            "return_pct": _number(getattr(row, f"return_{horizon}d_pct")),
            "alpha_vs_btc_pct": _number(getattr(row, f"alpha_vs_btc_{horizon}d_pct")),
        }

    return PriorOutcome(
        record_id=str(row.id),
        recommendation=str(row.recommendation or ""),
        entry_price_usd=_number(row.entry_price_usd),
        entry_captured_at=row.entry_captured_at,
        btc_price_at_entry=_number(row.btc_price_at_entry),
        marks=marks,
        signposts=_strings(row.signposts, limit=_SIGNPOST_LIMIT, chars=_SIGNPOST_CHARS),
        review_date=_as_date(row.review_date),
        linked_by=linked_by,
    )


def _days_since(when: datetime | None) -> int | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - when).days, 0)


async def get_prior_evaluation(
    project_name: str,
    coingecko_id: str | None = None,
    *,
    exclude_evaluation_id: str | None = None,
) -> PriorEvaluation | None:
    """The most recent completed prior evaluation of this project, or ``None``.

    Never raises for a missing or degraded prior — see the module docstring for
    the four states and what each returns. A database failure is logged and
    answered with ``None``, because a re-evaluation must not fail because the
    memory lookup did.
    """
    from app.database import async_session

    try:
        async with async_session() as session:
            return await _get_prior_evaluation(
                session, project_name, coingecko_id, exclude_evaluation_id
            )
    except Exception as exc:  # pragma: no cover - defensive, exercised by hand
        logger.warning("Prior-evaluation lookup failed for %s (non-fatal): %s", project_name, exc)
        return None


async def _get_prior_evaluation(
    session: Any,
    project_name: str,
    coingecko_id: str | None,
    exclude_evaluation_id: str | None,
) -> PriorEvaluation | None:
    from sqlalchemy import text as sql_text

    projects, matched_by = await _resolve_project(session, project_name, coingecko_id)
    if not projects:
        return None

    by_id = {row.id: row for row in projects}
    exclude: uuid.UUID | None = None
    if exclude_evaluation_id:
        try:
            exclude = uuid.UUID(str(exclude_evaluation_id))
        except (ValueError, AttributeError, TypeError):
            exclude = None

    candidates: Sequence[Any] = (
        await session.execute(
            sql_text(
                """
                SELECT id, project_id, status, created_at, completed_at, error
                  FROM evaluations
                 WHERE project_id = ANY(:pids)
                   AND status <> 'running'
                   -- CAST(... AS uuid), not `:exclude::uuid`: SQLAlchemy reads
                   -- `::` as an escaped colon and leaves the bind unsubstituted.
                   AND (CAST(:exclude AS uuid) IS NULL OR id <> CAST(:exclude AS uuid))
                 ORDER BY coalesce(completed_at, created_at) DESC
                 LIMIT :cap
                """
            ),
            {"pids": list(by_id.keys()), "exclude": exclude, "cap": MAX_CANDIDATES},
        )
    ).fetchall()

    if not candidates:
        return None

    skipped: list[str] = []
    fallback: PriorEvaluation | None = None

    for row in candidates:
        prior = await _build_prior(session, row, by_id[row.project_id], matched_by, skipped)
        if prior.usable:
            return prior
        skipped.append(str(row.id))
        if fallback is None:
            fallback = prior

    # Every candidate was a failed run, a failed adjudication, or an empty one.
    # That is itself worth one line in the report — "the last evaluation did not
    # complete" is different information from "this project has never been
    # evaluated" — so the newest is returned with the reason attached.
    if fallback is not None:
        others = [eid for eid in skipped if eid != fallback.evaluation_id]
        return PriorEvaluation(**{**fallback.__dict__, "skipped_unusable": others})
    return None


async def _build_prior(
    session: Any,
    evaluation: Any,
    project: Any,
    matched_by: str,
    skipped: list[str],
) -> PriorEvaluation:
    """Assemble one candidate. Prefers ``reports``; falls back to ``agent_outputs``."""
    anchor = evaluation.completed_at or evaluation.created_at
    base: dict[str, Any] = {
        "evaluation_id": str(evaluation.id),
        "project_id": str(project.id),
        "project_name": str(project.name),
        "coingecko_id": project.coingecko_id,
        "matched_by": matched_by,
        "evaluated_at": anchor,
        "days_since": _days_since(anchor),
        "status": str(evaluation.status or ""),
        "skipped_unusable": list(skipped),
    }

    if str(evaluation.status or "").lower() != "completed":
        reason = f"the run ended with status '{evaluation.status}'"
        if evaluation.error:
            reason += f" ({_clip(evaluation.error, 160)})"
        return PriorEvaluation(source="none", usable=False, unusable_reason=reason, **base)

    report = await _load_report_row(session, evaluation.id)
    outputs = await _load_agent_outputs(session, evaluation.id)
    chair = outputs["chair"] if isinstance(outputs["chair"], dict) else {}

    if report is None and not outputs["has_any"]:
        return PriorEvaluation(
            source="none",
            usable=False,
            unusable_reason="the run left no report and no agent outputs",
            **base,
        )

    source = "reports" if report is not None else "agent_outputs"

    chair_error = chair.get("error") or chair.get("parse_error") or outputs["chair_error"]
    decision = str(chair.get("decision") or "").strip().upper()
    if not decision and report is not None:
        decision = str(report["recommendation"] or "").strip().upper()

    if not decision or decision in _NON_VERDICTS:
        reason = "the Chair produced no usable decision"
        if chair_error:
            reason += f" ({_clip(chair_error, 160)})"
        elif decision:
            reason = f"the run was recorded as {decision}"
        return PriorEvaluation(source=source, usable=False, unusable_reason=reason, **base, decision=decision or None)

    # Signposts: three possible homes, in descending order of authority. The
    # ledger's copy is the one a checkpoint would grade against, but it only
    # started being written on 2026-08-25 (migration 0003), so for every
    # historical evaluation the Chair's own agent output is the only copy.
    calib_decision = decision
    outcome = await _load_outcome(
        session, evaluation.id, str(project.name), project.coingecko_id, anchor, calib_decision
    )

    signposts: list[str] = []
    signposts_source = "none"
    if outcome is not None and outcome.signposts:
        signposts, signposts_source = outcome.signposts, "calibration"
    if not signposts:
        chair_signposts = _strings(
            chair.get("signposts"), limit=_SIGNPOST_LIMIT, chars=_SIGNPOST_CHARS
        )
        if chair_signposts:
            signposts, signposts_source = chair_signposts, "chair"
    if not signposts:
        raw = report["report_signposts"] if report is not None else outputs["report_signposts"]
        report_signposts = _strings(raw, limit=_SIGNPOST_LIMIT, chars=_SIGNPOST_CHARS)
        if report_signposts:
            signposts, signposts_source = report_signposts, "report"

    review_date = (
        (outcome.review_date if outcome is not None else None)
        or _as_date(chair.get("review_date"))
        or _as_date(report["review_date"] if report is not None else None)
    )

    key_risks = _strings(
        report["key_risks"] if report is not None else outputs["key_risks"],
        limit=_RISK_LIMIT,
        chars=_RISK_CHARS,
    )

    overall = report["overall_score"] if report is not None else outputs["overall_score"]
    if overall is None:
        overall = _number(chair.get("score"))

    exec_summary = report["exec_summary"] if report is not None else outputs["exec_summary"]
    report_rec = (
        str(report["recommendation"] or "") if report is not None else str(outputs["recommendation"] or "")
    )

    return PriorEvaluation(
        source=source,
        usable=True,
        decision=decision,
        conviction=str(chair.get("conviction_level") or "") or None,
        overall_score=overall,
        report_recommendation=report_rec.strip().upper() or None,
        chair_summary=_clip(chair.get("summary") or chair.get("reasoning") or "", _SUMMARY_CHARS),
        executive_summary=_clip(exec_summary, _SUMMARY_CHARS),
        signposts=signposts,
        signposts_source=signposts_source,
        review_date=review_date,
        key_risks=key_risks,
        outcome=outcome,
        report_version=(report["version"] if report is not None else None),
        **base,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _fit(blocks: list[list[str]], limit: int) -> str:
    """Join blocks under a character budget, dropping from the LOW-priority end.

    ``blocks`` is in descending priority. A blunt tail slice would have cut the
    price outcome off the Hyperliquid block — 2,911 characters, with the
    signposts and the eight-item risk list ahead of it — which is exactly the
    line the delta section most needs. Trimming per block keeps the short,
    high-value facts and sheds the long lists.
    """
    out: list[str] = []
    used = 0
    dropped = 0
    for block in blocks:
        for line in block:
            cost = len(line) + 1
            if used + cost > limit:
                dropped += 1
                continue
            out.append(line)
            used += cost
    if dropped:
        note = f"[{dropped} further prior-context line(s) omitted for prompt budget]"
        while out and used + len(note) + 1 > limit:
            used -= len(out.pop()) + 1
        out.append(note)
    return "\n".join(out)


def render_prior_context(prior: PriorEvaluation | None, limit: int = PRIOR_CONTEXT_CHAR_LIMIT) -> str:
    """The bounded prompt block. Empty string when there is nothing to say.

    Empty is load-bearing: a first-time evaluation must add no text, no section
    and no tokens, so every caller keys off the truthiness of this string.
    """
    if prior is None:
        return ""

    when = prior.evaluated_at.strftime("%Y-%m-%d") if prior.evaluated_at else "an unknown date"
    age = f" ({prior.days_since} days ago)" if prior.days_since is not None else ""
    header = [f"PREVIOUS EVALUATION OF {prior.project_name.upper()} — {when}{age}"]

    if not prior.usable:
        header.append(
            f"The previous run on {when} produced no usable verdict: "
            f"{prior.unusable_reason}. There is no prior decision to compare against."
        )
        return _fit([header], limit)

    score = "not computed" if prior.overall_score is None else f"{prior.overall_score:g}/100"
    conviction = f", conviction {prior.conviction}" if prior.conviction else ""
    header.append(f"Decision: {prior.decision} — composite score {score}{conviction}.")
    if prior.report_recommendation and prior.report_recommendation != prior.decision:
        header.append(
            f"The Report Writer had recommended {prior.report_recommendation}; "
            f"the Chair decided {prior.decision}."
        )

    review: list[str] = []
    if prior.review_date:
        state = "now due or overdue" if prior.review_date_passed else "not yet due"
        review.append(f"Review date set: {prior.review_date.isoformat()} ({state}).")

    # Ordered before the prose and the lists: it is four short lines and it is
    # the only thing in the block that is a fact about the world rather than a
    # restatement of what the committee believed.
    outcome_lines: list[str] = []
    outcome = prior.outcome
    if outcome is not None:
        entry_day = outcome.entry_captured_at.strftime("%Y-%m-%d") if outcome.entry_captured_at else "?"
        price = "unknown" if outcome.entry_price_usd is None else f"${outcome.entry_price_usd:,.6g}"
        outcome_lines.append(
            f"Entry mark: {price} on {entry_day} (calibration record {outcome.record_id[:8]})."
        )
        graded = outcome.graded_horizons
        if graded:
            for horizon in graded:
                mark = outcome.marks[horizon]
                outcome_lines.append(
                    f"  {horizon}d: return {_pct(mark['return_pct'])}, "
                    f"alpha vs BTC {_pct(mark['alpha_vs_btc_pct'])}."
                )
        else:
            outcome_lines.append(
                "  No checkpoint has been graded yet, so the outcome is not yet measurable."
            )
    else:
        outcome_lines.append(
            "No calibration record was written for that evaluation, so there is no price mark."
        )

    summary = [f"Chair's rationale: {prior.chair_summary}"] if prior.chair_summary else []

    signposts: list[str] = []
    if prior.signposts:
        signposts.append(
            f"Signposts the Chair named as what would change its mind ({prior.signposts_source}):"
        )
        signposts.extend(f"  {i}. {s}" for i, s in enumerate(prior.signposts, 1))

    risks: list[str] = []
    if prior.key_risks:
        risks.append("Top risks named last time:")
        risks.extend(f"  - {r}" for r in prior.key_risks)

    return _fit([header, review, outcome_lines, summary, signposts, risks], limit)
