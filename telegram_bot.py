#!/usr/bin/env python3
"""Telegram bot for Committee Orchestrator.

Send a project name and get back an investment committee report.

Formats:
  Polkadot DOT polkadot L1     (full: name ticker coingecko_id category)
  Aave AAVE aave DeFi          (full)
  Polkadot                     (auto-resolve via CoinGecko)
"""
import os
import asyncio
import io
import re
import uuid
import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("committee-bot")

# python-telegram-bot puts the bot token in the URL PATH, and httpx logs every
# request line at INFO. Left alone, that writes the full token into the journal
# on every poll — roughly a quarter of a million times a month, in cleartext,
# readable by anything that can read logs or a /var/log backup. This is not
# hypothetical: it is how the previous token was found.
#
# WARNING is enough to keep genuine transport failures visible.
for _noisy in ("httpx", "httpcore", "telegram.ext.Updater", "telegram.request"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Required env vars (load via your .env or your shell):
#   TELEGRAM_BOT_TOKEN         - from @BotFather
#   TELEGRAM_ALLOWED_CHAT_ID   - numeric Telegram user/chat ID permitted to use this bot
#   COMMITTEE_API_BASE         - internal FastAPI URL  (default: http://localhost:8100)
#   COMMITTEE_REPORT_BASE      - public report URL     (default: same as COMMITTEE_API_BASE)
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "0"))
if not ALLOWED_CHAT_ID:
    # 0 matches no real Telegram chat, so an unset allowlist does not fail open —
    # it fails *silent*: the bot polls forever and ignores every message, which
    # looks identical to a healthy bot. Say so at startup.
    logging.getLogger("committee-bot").error(
        "TELEGRAM_ALLOWED_CHAT_ID is not set — every message will be ignored. "
        "Set it in the .env next to this script."
    )
API_BASE = os.environ.get("COMMITTEE_API_BASE", "http://localhost:8100")
REPORT_BASE = os.environ.get("COMMITTEE_REPORT_BASE", API_BASE)


PENDING = {}


class _Reply:
    """Adapts a Message so the evaluation body can keep calling
    update.message.reply_text after being moved out of the handler."""

    def __init__(self, message):
        self.message = message


def get_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def resolve_candidates(query, limit=3):
    """Rank CoinGecko search hits for `query`, best match first.

    CoinGecko's /search sorts by market cap, NOT by how well the result matches
    what you typed. Taking coins[0] therefore returns the *largest* coin whose
    name or symbol merely mentions the query. Searching "SERV" put Ethereum Name
    Service (rank 153) first and OpenServ — the only exact symbol match, rank 811
    — twelfth. The committee then spent ~$2 evaluating the wrong asset.

    So rank explicitly: exact symbol, then exact name, then symbol prefix, then
    CoinGecko's own order as the tiebreak.
    """
    q = (query or "").strip().lower()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/search", params={"query": query}
            )
            r.raise_for_status()
            coins = r.json().get("coins", [])
    except Exception as exc:
        logger.error("CoinGecko resolve failed: %s", exc)
        return []

    def rank(idx_coin):
        idx, c = idx_coin
        sym = (c.get("symbol") or "").lower()
        nm = (c.get("name") or "").lower()
        if sym == q:
            return (0, idx)
        if nm == q:
            return (1, idx)
        if sym.startswith(q) or nm.startswith(q):
            return (2, idx)
        return (3, idx)

    ordered = [c for _, c in sorted(enumerate(coins), key=rank)]
    out = []
    for c in ordered[:limit]:
        out.append({
            "project_name": c.get("name") or query,
            "ticker": (c.get("symbol") or "").upper(),
            "coingecko_id": c.get("id") or "",
            "category": "",          # filled from /coins/{id}; never assumed
            "market_cap_rank": c.get("market_cap_rank"),
        })
    return out


async def fetch_category(coingecko_id):
    """Real category from CoinGecko, because the old code hardcoded "L1".

    Every auto-resolved project was labelled L1 — a naming service, a DeFi
    protocol, a stablecoin, all "L1" — and that label is passed to the agents as
    fact.
    """
    if not coingecko_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/coins/" + coingecko_id,
                params={"localization": "false", "tickers": "false",
                        "market_data": "false", "community_data": "false",
                        "developer_data": "false"},
            )
            r.raise_for_status()
            cats = [c for c in (r.json().get("categories") or []) if c]
            return cats[0] if cats else ""
    except Exception as exc:
        logger.warning("Category lookup failed for %s: %s", coingecko_id, exc)
        return ""


def parse_message(text):
    """Parse user message into project details."""
    parts = text.strip().split()
    if len(parts) >= 4:
        return {
            "project_name": parts[0],
            "ticker": parts[1].upper(),
            "coingecko_id": parts[2].lower(),
            "category": parts[3],
        }
    elif len(parts) == 3:
        return {
            "project_name": parts[0],
            "ticker": parts[1].upper(),
            "coingecko_id": parts[2].lower(),
            "category": "L1",
        }
    elif len(parts) == 2:
        return {
            "project_name": parts[0],
            "ticker": parts[1].upper(),
            "coingecko_id": parts[0].lower(),
            "category": "L1",
        }
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    explicit = parse_message(text)
    if explicit:
        candidates = [dict(explicit, market_cap_rank=None)]
    else:
        await update.message.reply_text("Resolving project...")
        candidates = await resolve_candidates(text)
        if not candidates:
            await update.message.reply_text(
                "Could not find '%s' on CoinGecko.\n"
                "Try the explicit form: Name Ticker CoinGeckoID Category" % text[:60]
            )
            return

    best = candidates[0]
    if not best.get("category"):
        best["category"] = await fetch_category(best["coingecko_id"]) or "Unknown"

    # Confirmation gate. A run is 15 agents, 5-10 minutes and roughly $2 of API
    # spend, and it writes a permanent row into the calibration ledger. Nothing
    # starts without an explicit tap.
    token = uuid.uuid4().hex[:12]
    PENDING[token] = {"chat_id": update.effective_chat.id, "candidates": candidates,
                      "chosen": 0, "query": text}

    rank = best.get("market_cap_rank")
    lines = [
        "Confirm before I run this:",
        "",
        "  %s (%s)" % (best["project_name"], best["ticker"]),
        "  CoinGecko: %s" % best["coingecko_id"],
        "  Category:  %s" % best["category"],
    ]
    if rank:
        lines.append("  Mkt cap rank: #%s" % rank)
    if not explicit and len(candidates) > 1:
        others = ", ".join(
            "%s (%s)" % (c["project_name"], c["ticker"]) for c in candidates[1:]
        )
        lines += ["", "Other matches for '%s': %s" % (text[:30], others)]
    lines += ["", "~5-10 min, ~$2 of API spend, and it is recorded in the ledger."]

    buttons = [[InlineKeyboardButton("Run evaluation", callback_data="go:" + token)]]
    for i, c in enumerate(candidates[1:], start=1):
        buttons.append([InlineKeyboardButton(
            "Use %s (%s) instead" % (c["project_name"][:22], c["ticker"]),
            callback_data="pick:%s:%d" % (token, i))])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="no:" + token)])

    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirm / switch-match / cancel buttons."""
    q = update.callback_query
    await q.answer()
    if q.message.chat.id != ALLOWED_CHAT_ID:
        return

    action, _, rest = q.data.partition(":")
    token = rest.split(":")[0]
    pending = PENDING.get(token)
    if not pending:
        await q.edit_message_text("That confirmation has expired — send the project again.")
        return

    if action == "no":
        PENDING.pop(token, None)
        await q.edit_message_text("Cancelled. Nothing was run and nothing was recorded.")
        return

    if action == "pick":
        idx = int(rest.split(":")[1])
        pending["chosen"] = idx
        c = pending["candidates"][idx]
        if not c.get("category"):
            c["category"] = await fetch_category(c["coingecko_id"]) or "Unknown"
        await q.edit_message_text(
            "Confirm before I run this:\n\n"
            "  %s (%s)\n  CoinGecko: %s\n  Category:  %s\n\n"
            "~5-10 min, ~$2 of API spend, and it is recorded in the ledger."
            % (c["project_name"], c["ticker"], c["coingecko_id"], c["category"]),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Run evaluation", callback_data="go:" + token)],
                [InlineKeyboardButton("Cancel", callback_data="no:" + token)],
            ]),
        )
        return

    PENDING.pop(token, None)
    project = pending["candidates"][pending["chosen"]]
    await q.edit_message_text(
        "Running evaluation for %s (%s)...\nCoinGecko ID: %s\nCategory: %s\n\n"
        "This takes 5-10 minutes."
        % (project["project_name"], project["ticker"],
           project["coingecko_id"], project["category"])
    )
    await run_evaluation(q.message, project)


# Mirrors backend/app/api/reports.py::_filename_slug. Alphanumerics survive,
# every other run collapses to a single "-", so no project name can produce a
# path separator, a leading dot, or anything Telegram would reject.
_FILENAME_KEEP_RE = re.compile(r"[^A-Za-z0-9]+")

# Content-Disposition is written by our own backend, but a filename taken from
# a header and handed to a file API gets validated anyway. Anything else falls
# back to the locally computed name.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,90}$")


def safe_filename(name, extension="md"):
    slug = _FILENAME_KEEP_RE.sub("-", str(name or "")).strip("-").lower()[:60].strip("-")
    return "aiic-%s.%s" % (slug or "report", extension)


def extract_summary(data):
    """Best available summary, mirroring api/evaluate.py::_extract_summary.

    The Chair emits `reasoning` on most runs and `summary` on some, so reading
    only `summary` printed "No summary available." under a real decision — the
    text existed, the bot was looking in one place for it. Walk the same chain
    the API walks: report writer's draft summary, then the Chair's summary,
    then the Chair's reasoning.
    """
    results = data.get("agent_results") or {}
    if not isinstance(results, dict):
        results = {}

    def output_of(agent):
        entry = results.get(agent) or {}
        out = entry.get("output") if isinstance(entry, dict) else None
        return out if isinstance(out, dict) else {}

    def first_text(source, *keys):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    # 1. draft_report.summary — the Report Writer's own one-paragraph summary.
    draft = output_of("report_writer")
    summary = first_text(draft, "summary")
    if summary:
        return summary

    # 2/3. the Chair's summary, then its reasoning.
    summary = first_text(output_of("committee_chair"), "summary", "reasoning")
    if summary:
        return summary

    # 4. the top-level field, for callers that pass the raw pipeline result.
    return first_text(data, "chair_reasoning") or "No summary available."


def format_score(value):
    """`68.2/100`, or an honest string when there is no score.

    "Score: None/100" was printed under INSUFFICIENT_DATA decisions, which
    reads as a scored zero-ish result rather than the absence of a score.
    """
    try:
        return "%g/100" % float(value)
    except (TypeError, ValueError):
        return "n/a (insufficient data)"


async def fetch_report_markdown(eval_id):
    """Download the markdown report. Returns (bytes, filename) or (None, None).

    Fetched over API_BASE — the in-container URL — not REPORT_BASE, so this
    works regardless of whether the public address is reachable. That is the
    point of attaching the file at all: REPORT_BASE is a Tailscale address and
    the links in the message below are dead on a phone that is off the tailnet.
    A document is not.
    """
    if not eval_id:
        return None, None
    url = "%s/api/reports/%s/markdown" % (API_BASE, eval_id)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"download": "1"})
        if r.status_code != 200:
            logger.warning("Report fetch for %s returned %s", eval_id, r.status_code)
            return None, None
        body = r.content
    except Exception as exc:
        logger.warning("Report fetch for %s failed: %s", eval_id, exc)
        return None, None

    if not body:
        logger.warning("Report for %s came back empty", eval_id)
        return None, None

    # Telegram's document limit is 50 MB; a report is ~30 KB. The guard is
    # here so a runaway report fails as a skipped attachment, not a rejected
    # API call that takes the whole result message down with it.
    if len(body) > 45 * 1024 * 1024:
        logger.warning("Report for %s is %d bytes — too large to attach", eval_id, len(body))
        return None, None

    filename = ""
    disposition = r.headers.get("content-disposition", "")
    match = re.search(r'filename="([^"\r\n]+)"', disposition)
    if match and _SAFE_FILENAME_RE.match(match.group(1)):
        filename = match.group(1)
    return body, filename


async def run_evaluation(message, project):
    name = project["project_name"]
    ticker = project["ticker"]
    cg_id = project["coingecko_id"]
    category = project["category"]
    update = _Reply(message)

    # Call evaluation API
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(
                API_BASE + "/api/evaluate",
                json={
                    "project_name": name,
                    "ticker": ticker,
                    "coingecko_id": cg_id,
                    "category": category,
                    "chain": name,
                },
            )
            if r.status_code != 200:
                await update.message.reply_text("Evaluation failed: %s" % r.text[:200])
                return

            data = r.json()
    except Exception as e:
        await update.message.reply_text("Evaluation error: %s" % str(e)[:200])
        return

    # Extract results
    rec = data.get("recommendation", "N/A")
    eval_id = data.get("evaluation_id", "")

    chair = data.get("agent_results", {}).get("committee_chair", {}).get("output", {})
    if not isinstance(chair, dict):
        chair = {}
    summary = extract_summary(data)

    report_url = "%s/api/reports/%s/html" % (REPORT_BASE, eval_id)
    md_url = "%s/api/reports/%s/markdown" % (REPORT_BASE, eval_id)

    msg = (
        "EVALUATION COMPLETE: %s (%s)\n\n"
        "Decision: %s\n"
        "Score: %s\n"
        "Conviction: %s\n\n"
        "%s\n\n"
        "Report: %s\n"
        "Markdown: %s"
    ) % (
        name, ticker,
        rec, format_score(data.get("overall_score")),
        chair.get("conviction_level", "N/A"),
        summary[:500],
        report_url, md_url,
    )

    await update.message.reply_text(msg)

    # The links above only resolve on the tailnet. Send the report itself too,
    # so it is readable on a phone anywhere. A failed fetch must not lose the
    # result message that already went out, so this is best-effort and every
    # failure path just tells Jacob to use the links.
    body, filename = await fetch_report_markdown(eval_id)
    if not body:
        await update.message.reply_text(
            "Could not attach the report file — use the links above."
        )
        return
    try:
        await update.message.reply_document(
            document=io.BytesIO(body),
            filename=filename or safe_filename(name),
            caption="%s (%s) — full committee report" % (name, ticker),
        )
    except Exception as exc:
        logger.warning("Sending the report document failed: %s", exc)
        await update.message.reply_text(
            "Could not attach the report file (%s) — use the links above." % str(exc)[:120]
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await update.message.reply_text(
        "Committee Orchestrator Bot\n\n"
        "Send a project name to evaluate:\n"
        "  Polkadot DOT polkadot L1\n"
        "  Aave AAVE aave DeFi\n"
        "  Chainlink  (auto-resolve)\n\n"
        "Format: Name Ticker CoinGeckoID Category\n"
        "Or just send the name for auto-resolve."
    )


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(API_BASE + "/api/reports")
            data = r.json()
        reports = data.get("reports", [])
        if not reports:
            await update.message.reply_text("No reports found.")
            return
        lines = ["Recent reports:\n"]
        for rpt in reports[:10]:
            eid = rpt["evaluation_id"]
            pname = rpt["project_name"]
            url = "%s/api/reports/%s/html" % (REPORT_BASE, eid)
            lines.append("%s: %s" % (pname, url))
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text("Error: %s" % str(e)[:200])


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(API_BASE + "/health")
            await update.message.reply_text("API: %s" % r.text[:200])
    except Exception as e:
        await update.message.reply_text("API down: %s" % str(e)[:100])


def main():
    token = get_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return
    logger.info(
        "Bot token loaded (id %s, secret withheld); allowed chat id %s",
        token.split(":", 1)[0] or "?", ALLOWED_CHAT_ID or "UNSET",
    )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(on_confirm))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Committee bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
