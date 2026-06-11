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
import logging
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("committee-bot")

# Required env vars (load via your .env or your shell):
#   TELEGRAM_BOT_TOKEN         - from @BotFather
#   TELEGRAM_ALLOWED_CHAT_ID   - numeric Telegram user/chat ID permitted to use this bot
#   COMMITTEE_API_BASE         - internal FastAPI URL  (default: http://localhost:8100)
#   COMMITTEE_REPORT_BASE      - public report URL     (default: same as COMMITTEE_API_BASE)
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "0"))
API_BASE = os.environ.get("COMMITTEE_API_BASE", "http://localhost:8100")
REPORT_BASE = os.environ.get("COMMITTEE_REPORT_BASE", API_BASE)


def get_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def resolve_project(name):
    """Try to auto-resolve project details via CoinGecko search."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": name}
            )
            data = r.json()
            coins = data.get("coins", [])
            if coins:
                coin = coins[0]
                return {
                    "project_name": coin.get("name", name),
                    "ticker": coin.get("symbol", "").upper(),
                    "coingecko_id": coin.get("id", ""),
                    "category": "L1",
                }
    except Exception as e:
        logger.error("CoinGecko resolve failed: %s", e)
    return None


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

    # Parse or resolve
    project = parse_message(text)
    if not project:
        await update.message.reply_text("Resolving project...")
        project = await resolve_project(text)
        if not project:
            await update.message.reply_text("Could not find project. Try: Name Ticker CoinGeckoID Category")
            return

    name = project["project_name"]
    ticker = project["ticker"]
    cg_id = project["coingecko_id"]
    category = project["category"]

    await update.message.reply_text(
        "Running evaluation for %s (%s)...\n"
        "CoinGecko ID: %s\n"
        "Category: %s\n\n"
        "This takes 5-10 minutes." % (name, ticker, cg_id, category)
    )

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
    status = data.get("status", "unknown")
    score = data.get("overall_score", "N/A")
    rec = data.get("recommendation", "N/A")
    eval_id = data.get("evaluation_id", "")

    chair = data.get("agent_results", {}).get("committee_chair", {}).get("output", {})
    summary = chair.get("summary", "No summary available.")
    reasoning = chair.get("reasoning", "")

    report_url = "%s/api/reports/%s/html" % (REPORT_BASE, eval_id)
    md_url = "%s/api/reports/%s/markdown" % (REPORT_BASE, eval_id)

    msg = (
        "EVALUATION COMPLETE: %s (%s)\n\n"
        "Decision: %s\n"
        "Score: %s/100\n"
        "Conviction: %s\n\n"
        "%s\n\n"
        "Report: %s\n"
        "Markdown: %s"
    ) % (
        name, ticker,
        rec, score,
        chair.get("conviction_level", "N/A"),
        summary[:500],
        report_url, md_url,
    )

    await update.message.reply_text(msg)


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

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Committee bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
