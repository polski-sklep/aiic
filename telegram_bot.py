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
import json
import re
import time
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


# ---------------------------------------------------------------------------
# Run cost
# ---------------------------------------------------------------------------
# The bot reports what an evaluation cost, and the rates and the arithmetic
# live in backend/app/llm/pricing.py — one definition, shared with whatever
# puts the number in the persisted record and on the HTML report.
#
# Loaded BY PATH rather than as `app.llm.pricing`, deliberately. Importing the
# package would execute app/llm/__init__.py -> app/utils/types.py, which needs
# TypeAliasType (Python 3.12+). This process is not the backend: it runs under
# the VPS system interpreter, which is 3.10.12, with only httpx and
# python-telegram-bot installed. A package import would work in every test and
# fail on the one machine that sends the messages.
#
# And it is guarded. A missing or broken pricing module must cost the message
# its cost line and nothing else — never the report it is attached to.
def _load_pricing():
    import importlib.util
    import sys

    name = "committee_bot_pricing"
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "backend", "app", "llm", "pricing.py",
    )
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module, which is not optional here. pricing.py
    # uses `from __future__ import annotations`, so @dataclass resolves its
    # field types lazily via `sys.modules[cls.__module__].__dict__` — with the
    # module absent from sys.modules that lookup returns None and the import
    # dies on the first dataclass. Caught only because the guard below turned
    # it into a missing cost line instead of a crash.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


try:
    pricing = _load_pricing()
except Exception as _exc:  # pragma: no cover - exercised only by a broken deploy
    pricing = None
    logger.warning("Run-cost pricing unavailable, cost line will be omitted: %s", _exc)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
# Why an explicit queue at all: python-telegram-bot processes updates one at a
# time unless `concurrent_updates` is set. While an evaluation was running
# inside its HTTP call, every later message sat in PTB's *internal* queue and
# got no reply of any kind — no "Resolving...", nothing — for the 13 minutes the
# first run took. Three projects sent in a row looked like a dead bot.
#
# The fix is not concurrency. Two evaluations at once would double the API spend
# and hammer CoinGecko's rate limit, which has bitten this project repeatedly.
# The fix is to take ownership of the waiting: acknowledge on receipt, keep the
# backlog somewhere we can see it, name it, price it, and persist it.
#
# QUEUE[0] is the head — the job being confirmed or run. Handlers only ever
# append; the worker task is the only thing that removes.
QUEUE = []

# Runtime-only registry for the confirmation gate: token -> confirm record.
# Deliberately NOT persisted; an asyncio.Event does not survive a restart and a
# confirmation that outlives the process is meaningless. Recovered jobs are
# re-confirmed from scratch.
PENDING = {}

# Set whenever the queue changes so the worker re-examines it without polling.
WAKE = asyncio.Event()

_WORKER = None

# Where the queue lives across restarts. See save_queue() for why JSON.
QUEUE_FILE = os.environ.get(
    "COMMITTEE_QUEUE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_queue.json"),
)

# Measured, not guessed: 11m31s for Hyperliquid, and one run at 14m+ under the
# deeper report format. 13 is the middle of the observed range and it is used
# only for ETAs, never as a limit on anything.
JOB_MINUTES = float(os.environ.get("COMMITTEE_JOB_MINUTES", "13"))

# ~$2.40 of Anthropic spend for 15 agents. Shown per job and summed across the
# queue, because three queued projects is a $7 decision, not three $2 ones.
JOB_COST_USD = float(os.environ.get("COMMITTEE_JOB_COST_USD", "2.40"))

# A job at the head waits this long for a tap before it is dropped. Without
# this, one unanswered confirmation blocks every job behind it forever.
CONFIRM_TIMEOUT = float(os.environ.get("COMMITTEE_CONFIRM_TIMEOUT", "900"))

# Watchdog, NOT a timeout. The HTTP call has no read timeout by design (see
# run_evaluation), so a wedged backend would otherwise block the queue silently
# and forever. At 30 minutes — twice the longest run ever observed — the worker
# says so, and repeats every 15, without touching the request.
STALL_WARN = float(os.environ.get("COMMITTEE_STALL_WARN", "1800"))
STALL_REPEAT = float(os.environ.get("COMMITTEE_STALL_REPEAT", "900"))

# Bounds the blast radius of a fat-fingered paste: 10 queued jobs is already
# ~$24 and over two hours.
MAX_QUEUE = int(os.environ.get("COMMITTEE_MAX_QUEUE", "10"))

# Only these keys are written to disk. asyncio objects and Telegram Message
# handles are runtime state and are kept out of the file by construction.
_PERSIST_FIELDS = (
    "id", "chat_id", "query", "explicit", "candidates", "chosen",
    "state", "created_at", "started_at", "recovered",
)


class _Reply:
    """Adapts a Message so the evaluation body can keep calling
    update.message.reply_text after being moved out of the handler."""

    def __init__(self, message):
        self.message = message


class _ChatTarget:
    """The reply_text / reply_document surface of a Message, bound to a chat id.

    The worker runs long after the message that queued the job, and a recovered
    job has no Message object at all — its originating update died with the old
    process. Everything downstream (run_evaluation, the report attachment) still
    talks in terms of `.reply_text` / `.reply_document`, so give it that shape
    over the raw Bot instead of rewriting it.
    """

    def __init__(self, bot, chat_id):
        self._bot = bot
        self._chat_id = chat_id

    async def reply_text(self, text, **kwargs):
        return await self._bot.send_message(chat_id=self._chat_id, text=text, **kwargs)

    async def reply_document(self, document, filename=None, caption=None, **kwargs):
        return await self._bot.send_document(
            chat_id=self._chat_id, document=document,
            filename=filename, caption=caption, **kwargs
        )


def get_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def wake_worker():
    WAKE.set()


# --- persistence -----------------------------------------------------------
#
# JSON file, not arq/Redis. Both are already installed and unused, and reaching
# for them here would mean a broker process, a serialisation contract, a second
# failure mode when Redis is down, and a worker that lives outside this script —
# a large change to gain durability for a queue whose realistic maximum depth is
# single digits and whose only writer is one asyncio task. A file that is
# rewritten on every state change and read once at startup gives the property
# that was actually missing (a redeploy stops eating pending work) with no new
# dependency and no new process to supervise. If the queue ever needs multiple
# workers or cross-host visibility, that is the moment to promote it to arq.

def save_queue():
    """Rewrite the queue file. Atomic, so a crash mid-write cannot truncate it."""
    try:
        payload = {"version": 1, "saved_at": time.time(),
                   "jobs": [{k: job.get(k) for k in _PERSIST_FIELDS} for job in QUEUE]}
        tmp = QUEUE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, QUEUE_FILE)
    except Exception as exc:
        # A queue that cannot persist is still a working queue. Never let this
        # take down the worker.
        logger.error("Could not save the queue to %s: %s", QUEUE_FILE, exc)


def load_queue():
    """Read the queue file. Returns a list of jobs; [] if there is nothing usable."""
    try:
        with open(QUEUE_FILE, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.error("Queue file %s is unreadable (%s) — starting empty", QUEUE_FILE, exc)
        return []

    jobs = []
    for raw in (payload.get("jobs") or []) if isinstance(payload, dict) else []:
        if not isinstance(raw, dict) or not raw.get("query"):
            continue
        # The file is a persisted input. An edited or corrupted one must not be
        # able to make the bot message a chat the allowlist would have refused.
        if raw.get("chat_id") != ALLOWED_CHAT_ID:
            logger.warning("Dropping a restored job for chat %s — not the allowed chat",
                           raw.get("chat_id"))
            continue
        job = {k: raw.get(k) for k in _PERSIST_FIELDS}
        job["id"] = job["id"] or uuid.uuid4().hex[:12]
        job["candidates"] = job["candidates"] if isinstance(job["candidates"], list) else []
        job["chosen"] = job["chosen"] if isinstance(job["chosen"], int) else 0
        if job["chosen"] >= len(job["candidates"]):
            job["chosen"] = 0
        # Anything that was mid-flight is restored as plain queued work. It will
        # go through the confirmation gate again before a penny is spent, which
        # is exactly the property that makes re-running it safe.
        job["recovered"] = bool(job.get("recovered")) or job.get("state") in (
            "running", "confirming")
        job["state"] = "queued"
        job["started_at"] = None
        job["resolving"] = False
        jobs.append(job)
    return jobs


# --- estimates -------------------------------------------------------------

def _job_remaining_minutes(job, now):
    """Minutes of work left in `job`. A running job is discounted by its elapsed
    time; anything not yet started costs a full run."""
    started = job.get("started_at")
    if started:
        left = JOB_MINUTES - (now - started) / 60.0
        # A run past its estimate is not finished — never report 0 minutes left.
        return left if left > 1.0 else 1.0
    return JOB_MINUTES


def queue_eta(index, now=None):
    """(minutes until this job starts, minutes until it finishes)."""
    now = time.time() if now is None else now
    start = sum(_job_remaining_minutes(j, now) for j in QUEUE[:index])
    return start, start + JOB_MINUTES


def fmt_minutes(minutes):
    if minutes <= 0:
        return "now"
    if minutes < 1:
        # Only reachable if someone tunes a timeout down to seconds. Say seconds
        # rather than rounding a real deadline to "now".
        return "~%d sec" % max(int(round(minutes * 60)), 1)
    minutes = int(round(minutes))
    if minutes < 60:
        return "~%d min" % minutes
    return "~%dh %02dm" % divmod(minutes, 60)


def fmt_cost(runs):
    return "~$%.2f" % (runs * JOB_COST_USD)


def job_label(job):
    """Best name we have: the resolved project once known, else what was typed."""
    cands = job.get("candidates") or []
    idx = job.get("chosen", 0)
    if idx < len(cands):
        c = cands[idx]
        return "%s (%s)" % (c.get("project_name") or job["query"], c.get("ticker") or "?")
    return job["query"][:40]


def position_of(job):
    """1-based queue position, or None if the job is gone."""
    for i, j in enumerate(QUEUE):
        if j is job:
            return i + 1
    return None


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

    if len(QUEUE) >= MAX_QUEUE:
        await update.message.reply_text(
            "Queue is full (%d jobs, %s). Wait for one to finish or /cancel something."
            % (len(QUEUE), fmt_cost(len(QUEUE)))
        )
        return

    # Resending the same name because the bot "looks stuck" used to be free.
    # Now it would buy a second $2.40 run of the same project.
    for existing in QUEUE:
        if existing["query"].strip().lower() == text.lower():
            await update.message.reply_text(
                "'%s' is already queued at position %d. Sending it again would run it twice."
                % (text[:40], position_of(existing))
            )
            return

    explicit = parse_message(text)
    job = {
        "id": uuid.uuid4().hex[:12],
        "chat_id": update.effective_chat.id,
        "query": text,
        "explicit": bool(explicit),
        "candidates": [dict(explicit, market_cap_rank=None)] if explicit else [],
        "chosen": 0,
        "state": "queued",
        "created_at": time.time(),
        "started_at": None,
        "recovered": False,
        # Set while the CoinGecko lookup below is in flight, so the worker does
        # not pick this job up before it knows what the project is.
        "resolving": not explicit,
    }
    QUEUE.append(job)
    save_queue()

    # ACKNOWLEDGE FIRST, before any network call. This is the whole point: the
    # user learns their message landed in well under a second, every time,
    # whatever else the bot is busy with.
    pos = len(QUEUE)
    starts, done = queue_eta(pos - 1)
    ack = ["Queued #%d: %s" % (pos, job_label(job) if explicit else text[:40])]
    if pos == 1:
        ack.append("Next up — I'll ask you to confirm in a moment.")
    else:
        ack.append("Position %d of %d. Starts %s, done %s."
                   % (pos, len(QUEUE), fmt_minutes(starts), fmt_minutes(done)))
        ack.append("I'll ask you to confirm when it reaches the front.")
    ack.append("Queue: %d %s, %s total."
               % (len(QUEUE), "run" if len(QUEUE) == 1 else "runs", fmt_cost(len(QUEUE))))
    await update.message.reply_text("\n".join(ack))

    # Resolve now rather than at the front of the queue, so a typo comes back in
    # seconds instead of half an hour. The confirmation itself still happens at
    # the front, where the money is spent.
    if not explicit:
        candidates = await resolve_candidates(text)
        job["resolving"] = False
        if not candidates:
            if position_of(job):
                QUEUE.remove(job)
            save_queue()
            wake_worker()
            await update.message.reply_text(
                "Could not find '%s' on CoinGecko — dropped from the queue.\n"
                "Try the explicit form: Name Ticker CoinGeckoID Category" % text[:60]
            )
            return
        job["candidates"] = candidates
        save_queue()

    wake_worker()


def _confirmation_view(job):
    """The confirmation card: text plus buttons. Rendered from the job so that
    the /pick path and the first render cannot drift apart."""
    candidates = job["candidates"]
    idx = job.get("chosen", 0)
    best = candidates[idx]
    token = job["id"]

    lines = [
        "Confirm before I run this:",
        "",
        "  %s (%s)" % (best["project_name"], best["ticker"]),
        "  CoinGecko: %s" % best["coingecko_id"],
        "  Category:  %s" % best["category"],
    ]
    if best.get("market_cap_rank"):
        lines.append("  Mkt cap rank: #%s" % best["market_cap_rank"])
    others = [c for i, c in enumerate(candidates) if i != idx]
    if not job.get("explicit") and others:
        lines += ["", "Other matches for '%s': %s" % (
            job["query"][:30],
            ", ".join("%s (%s)" % (c["project_name"], c["ticker"]) for c in others),
        )]
    lines += ["", "Typically ~%d min, %s of API spend, and it is recorded in the ledger."
              % (int(JOB_MINUTES), fmt_cost(1))]
    waiting = len(QUEUE) - 1
    if waiting > 0:
        lines.append("%d more queued behind this one (%s)." % (waiting, fmt_cost(waiting)))
    lines.append("No tap within %s and I drop it and move on."
                 % fmt_minutes(CONFIRM_TIMEOUT / 60.0))

    buttons = [[InlineKeyboardButton("Run evaluation", callback_data="go:" + token)]]
    for i, c in enumerate(candidates):
        if i == idx:
            continue
        buttons.append([InlineKeyboardButton(
            "Use %s (%s) instead" % (c["project_name"][:22], c["ticker"]),
            callback_data="pick:%s:%d" % (token, i))])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="no:" + token)])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirm / switch-match / cancel buttons.

    The gate itself is unchanged — a run costs real money and writes a permanent
    ledger row, so nothing starts without a tap. What changed is *when* it is
    asked: at the front of the queue, not on receipt, because confirming a job
    that will not start for another 25 minutes tells the user nothing useful.
    """
    q = update.callback_query
    await q.answer()
    if q.message.chat.id != ALLOWED_CHAT_ID:
        return

    action, _, rest = q.data.partition(":")
    token = rest.split(":")[0]
    record = PENDING.get(token)
    if not record:
        await q.edit_message_text("That confirmation has expired — send the project again.")
        return
    job = record["job"]

    if action == "no":
        record["decision"] = "no"
        record["event"].set()
        await q.edit_message_text("Cancelled. Nothing was run and nothing was recorded.")
        return

    if action == "pick":
        idx = int(rest.split(":")[1])
        if 0 <= idx < len(job["candidates"]):
            job["chosen"] = idx
            c = job["candidates"][idx]
            if not c.get("category"):
                c["category"] = await fetch_category(c["coingecko_id"]) or "Unknown"
            save_queue()
            text, markup = _confirmation_view(job)
            await q.edit_message_text(text, reply_markup=markup)
        return

    record["decision"] = "go"
    record["event"].set()
    project = job["candidates"][job["chosen"]]
    await q.edit_message_text(
        "Running evaluation for %s (%s)...\nCoinGecko ID: %s\nCategory: %s\n\n"
        "Typically 11-15 minutes; the deeper report format can make it longer."
        % (project["project_name"], project["ticker"],
           project["coingecko_id"], project["category"])
    )


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

async def _next_job():
    """Block until the head of the queue is ready to be worked on."""
    while True:
        WAKE.clear()
        if QUEUE and not QUEUE[0].get("resolving"):
            return QUEUE[0]
        await WAKE.wait()


async def _ask_confirmation(bot, job):
    """Put the confirmation card in front of the user and wait for a tap.

    Returns "go", "no" or "expired". Expiry is the important one: a job that is
    never answered must not hold the queue. It is dropped, said out loud, and
    the next job starts.
    """
    # Register BEFORE the category lookup and the send. Both are network calls,
    # and a /cancel arriving in that window would otherwise find nothing to
    # cancel — leaving the worker waiting 15 minutes on a card for a job the
    # user has already been told is gone.
    record = {"job": job, "event": asyncio.Event(), "decision": None, "message": None}
    PENDING[job["id"]] = record
    try:
        best = job["candidates"][job["chosen"]]
        if not best.get("category"):
            best["category"] = await fetch_category(best["coingecko_id"]) or "Unknown"
        if record["decision"]:
            return record["decision"]

        prefix = ""
        if job.get("recovered"):
            prefix = ("Recovered after a restart — this one was mid-run, so check "
                      "/reports first in case it already finished.\n\n")
            job["recovered"] = False
        text, markup = _confirmation_view(job)
        message = await bot.send_message(chat_id=job["chat_id"], text=prefix + text,
                                         reply_markup=markup)
        record["message"] = message

        await asyncio.wait_for(record["event"].wait(), timeout=CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            await record["message"].edit_text(
                "Expired — no confirmation within %s, so I did not run %s.\n"
                "Nothing was spent. Send it again when you want it."
                % (fmt_minutes(CONFIRM_TIMEOUT / 60.0), job_label(job))
            )
        except Exception as exc:
            logger.warning("Could not edit the expired confirmation: %s", exc)
        return "expired"
    finally:
        PENDING.pop(job["id"], None)
    return record["decision"] or "no"


async def _run_watched(target, project):
    """Run the evaluation, complaining if it runs absurdly long.

    The HTTP call has no read timeout on purpose (run_evaluation explains why),
    which means a wedged backend would block this queue forever with no signal —
    the bot would look healthy and idle. So watch the clock without touching the
    request: say something at STALL_WARN, repeat every STALL_REPEAT, never
    cancel. Nothing here can cut a working evaluation short.
    """
    task = asyncio.ensure_future(run_evaluation(target, project))
    waited = 0.0
    try:
        while True:
            interval = STALL_WARN if waited == 0 else STALL_REPEAT
            done, _pending = await asyncio.wait({task}, timeout=interval)
            if done:
                return await task  # re-raises anything the evaluation raised
            waited += interval
            logger.warning("Evaluation for %s has been running %d min",
                           project.get("project_name"), int(waited / 60))
            try:
                await target.reply_text(
                    "Still running %s (%s) after %d minutes — well past the usual %d.\n"
                    "I have not cancelled it and I will not; check the backend "
                    "(/health) if this looks wedged. %d job(s) waiting behind it."
                    % (project.get("project_name"), project.get("ticker"),
                       int(waited / 60), int(JOB_MINUTES), max(len(QUEUE) - 1, 0))
                )
            except Exception as exc:
                logger.warning("Could not send the stall warning: %s", exc)
    except asyncio.CancelledError:
        # Shutting down. Take the in-flight request with us rather than leaving
        # an orphaned task for the loop to complain about — the job itself is
        # preserved in the queue file by _process.
        task.cancel()
        raise


async def _process(bot, job):
    """Confirm, run, and retire the job — unless we are being shut down."""
    target = _ChatTarget(bot, job["chat_id"])
    retire = True
    try:
        job["state"] = "confirming"
        save_queue()
        decision = await _ask_confirmation(bot, job)
        if decision != "go":
            return

        job["state"] = "running"
        job["started_at"] = time.time()
        save_queue()
        await _run_watched(target, job["candidates"][job["chosen"]])
    except Exception as exc:
        # One bad job must not take the worker with it, and it must not vanish
        # quietly either. run_evaluation reports the failures it expects; this
        # covers everything else, including a crash while asking for
        # confirmation, when there is no other message to hang the news on.
        logger.exception("Job %s (%s) failed", job["id"], job["query"])
        try:
            await target.reply_text(
                "%s failed: %s: %s\nNothing further will be retried for it. "
                "Moving on to the next job."
                % (job_label(job), type(exc).__name__, str(exc)[:200])
            )
        except Exception as send_exc:
            logger.error("Could not report the failure: %s", send_exc)
    except asyncio.CancelledError:
        # Shutdown, not completion. Put the job back to plain `queued` and leave
        # it in the queue so it reaches the file intact — this is the exact case
        # that used to eat pending work when the bot was redeployed.
        retire = False
        if job["state"] == "running":
            # The backend was already working on this. It very likely finishes
            # and writes its report with nobody listening, so flag it: the next
            # process must say so rather than silently charging for it twice.
            job["recovered"] = True
        job["state"] = "queued"
        job["started_at"] = None
        raise
    finally:
        if retire and position_of(job):
            QUEUE.remove(job)
            if QUEUE:
                logger.info("Job finished; %d left in the queue", len(QUEUE))
        save_queue()
        wake_worker()


async def queue_worker(bot):
    """Drain the queue, one job at a time, forever.

    Every failure mode here ends in `continue`. A worker that dies leaves a bot
    that answers /health, acknowledges messages, queues them — and never runs
    another evaluation, with nothing in the logs to say why.
    """
    logger.info("Queue worker started")
    while True:
        try:
            job = await _next_job()
            await _process(bot, job)
        except asyncio.CancelledError:
            logger.info("Queue worker stopping")
            raise
        except Exception:
            logger.exception("Queue worker hit an unexpected error — continuing")
            await asyncio.sleep(1)


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


def cost_line(data):
    """The run-cost line for a completed evaluation, or "" if it cannot be had.

    Wrapped whole in a try/except on purpose. This is a courtesy line on a
    notification; the report it accompanies took thirteen minutes and real
    money. No arithmetic here is allowed to be the reason Jacob does not get it.
    """
    if pricing is None:
        return ""
    try:
        return pricing.format_cost_line(pricing.price_run(data.get("agent_results", {})))
    except Exception as exc:
        logger.warning("Could not price this run: %s", exc)
        return ""


def format_completion_message(data, name, ticker, report_url, md_url):
    """Build the completion notification. Pure — no network, no Telegram."""
    chair = data.get("agent_results", {}).get("committee_chair", {}).get("output", {})
    if not isinstance(chair, dict):
        chair = {}

    # The cost joins the run-metadata block rather than trailing after the
    # links: it is a fact about this run, and it is read at a glance with the
    # decision, not hunted for at the bottom.
    head = [
        "Decision: %s" % data.get("recommendation", "N/A"),
        "Score: %s" % format_score(data.get("overall_score")),
        "Conviction: %s" % chair.get("conviction_level", "N/A"),
    ]
    cost = cost_line(data)
    if cost:
        head.append(cost)

    return (
        "EVALUATION COMPLETE: %s (%s)\n\n"
        "%s\n\n"
        "%s\n\n"
        "Report: %s\n"
        "Markdown: %s"
    ) % (name, ticker, "\n".join(head), extract_summary(data)[:500], report_url, md_url)


async def run_evaluation(message, project):
    name = project["project_name"]
    ticker = project["ticker"]
    cg_id = project["coingecko_id"]
    category = project["category"]
    update = _Reply(message)

    # Call evaluation API
    try:
        # NO READ TIMEOUT, deliberately. Not 600s, not 1200s — none.
        #
        # A real Hyperliquid run took 11m31s and succeeded — 15 agents, report
        # persisted — and the bot reported "Evaluation error:" at exactly 10:00
        # because its own read timeout fired. The user saw a failure for work
        # that had completed. Raising the number just moves the cliff: runs are
        # now 14m+ under the deeper report format. Any read timeout is a bet
        # that the client can predict how long the committee will think, and
        # that bet has been lost twice. So the client simply waits.
        #
        # The CONNECT timeout stays at 15s, and it is a different thing. It
        # fires only when the backend cannot be reached at all — down, or
        # restarting. Without it, a dead backend is indistinguishable from a
        # long run and the bot hangs silently forever. Establishing a TCP
        # connection has nothing to do with how long the pipeline takes.
        #
        # A genuinely wedged backend is caught by the queue worker's watchdog
        # (_run_watched), which reports it without cancelling the request.
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=15.0)) as client:
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
    eval_id = data.get("evaluation_id", "")

    report_url = "%s/api/reports/%s/html" % (REPORT_BASE, eval_id)
    md_url = "%s/api/reports/%s/markdown" % (REPORT_BASE, eval_id)

    await update.message.reply_text(
        format_completion_message(data, name, ticker, report_url, md_url)
    )

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
        "Or just send the name for auto-resolve.\n\n"
        "Projects queue up and run one at a time (~%d min, %s each).\n"
        "  /queue        what is pending, with ETAs and the running total\n"
        "  /cancel 2     drop queue position 2\n"
        "  /cancel all   drop everything not yet running\n"
        "  /reports /health"
        % (int(JOB_MINUTES), fmt_cost(1))
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """What is pending, where, when, and what it will cost."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not QUEUE:
        await update.message.reply_text("Queue is empty. Send a project name to start one.")
        return

    now = time.time()
    lines = ["Queue: %d %s, %s total"
             % (len(QUEUE), "job" if len(QUEUE) == 1 else "jobs", fmt_cost(len(QUEUE))), ""]
    for i, job in enumerate(QUEUE):
        starts, done = queue_eta(i, now)
        if job["state"] == "running":
            elapsed = (now - (job.get("started_at") or now)) / 60.0
            status = "running, %d min in, done %s" % (int(elapsed), fmt_minutes(done - starts))
        elif job["state"] == "confirming":
            status = "waiting for your confirmation"
        else:
            status = "queued, starts %s, done %s" % (fmt_minutes(starts), fmt_minutes(done))
        lines.append("%d. %s — %s" % (i + 1, job_label(job), status))
    lines += ["", "/cancel <position> to drop one."]
    await update.message.reply_text("\n".join(lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel <position> or /cancel all. A running job is never killed."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    args = (update.message.text or "").split()[1:]
    if not QUEUE:
        await update.message.reply_text("Queue is empty — nothing to cancel.")
        return
    if not args:
        await update.message.reply_text(
            "Which one? /queue lists positions, then /cancel 2 — or /cancel all."
        )
        return

    async def drop(job):
        """Retire one job. If its confirmation card is up, take the live button
        off it too — a cancelled job must not leave a tappable 'Run evaluation'
        sitting in the chat."""
        record = PENDING.get(job["id"])
        if record:
            record["decision"] = "no"
            record["event"].set()
            if record.get("message"):
                try:
                    await record["message"].edit_text(
                        "Cancelled from /cancel. %s was not run and nothing was recorded."
                        % job_label(job)
                    )
                except Exception as exc:
                    logger.warning("Could not edit the cancelled card: %s", exc)
            return True          # the worker retires it
        if position_of(job):
            QUEUE.remove(job)
        return False

    if args[0].lower() == "all":
        # Everything that has not started. A running evaluation has already been
        # paid for and is writing a ledger row; dropping it here would leave the
        # backend running with nobody listening for the result.
        dropped = [j for j in QUEUE if j["state"] != "running"]
        for job in dropped:
            await drop(job)
        save_queue()
        wake_worker()
        kept = " The running job continues." if QUEUE else ""
        await update.message.reply_text(
            "Cancelled %d queued %s (%s not spent).%s"
            % (len(dropped), "job" if len(dropped) == 1 else "jobs",
               fmt_cost(len(dropped)), kept)
        )
        return

    try:
        pos = int(args[0])
    except ValueError:
        await update.message.reply_text("Give me a position number, e.g. /cancel 2 — or 'all'.")
        return
    if not 1 <= pos <= len(QUEUE):
        await update.message.reply_text(
            "There is no position %d. The queue has %d job(s)." % (pos, len(QUEUE))
        )
        return

    job = QUEUE[pos - 1]
    if job["state"] == "running":
        await update.message.reply_text(
            "%s is already running — I will not kill it mid-evaluation. "
            "It has been paid for and the ledger row is being written."
            % job_label(job)
        )
        return

    label = job_label(job)
    # If it is at the front with a card up, the worker retires it, so there is
    # exactly one place that removes jobs from the queue.
    worker_will_retire = await drop(job)
    save_queue()
    wake_worker()
    await update.message.reply_text(
        "Cancelled %s. %s not spent. %d left in the queue."
        % (label, fmt_cost(1), max(len(QUEUE) - (1 if worker_will_retire else 0), 0))
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


async def on_startup(app):
    """Reload the queue and start the worker, before polling begins."""
    global _WORKER

    restored = load_queue()
    QUEUE.extend(restored)
    save_queue()

    _WORKER = asyncio.ensure_future(queue_worker(app.bot))
    wake_worker()

    if not restored:
        return
    logger.info("Recovered %d job(s) from %s", len(restored), QUEUE_FILE)
    lines = ["Restarted. %d queued %s survived and %s still pending:"
             % (len(restored), "job" if len(restored) == 1 else "jobs",
                fmt_cost(len(restored)))]
    for i, job in enumerate(restored, start=1):
        lines.append("%d. %s" % (i, job_label(job)))
    lines.append("")
    interrupted = [j for j in restored if j.get("recovered")]
    if interrupted:
        lines.append(
            "%d of these was already running when the process stopped (%s). The "
            "backend may well have finished it — check /reports before you confirm "
            "it again, or you pay for it twice."
            % (len(interrupted), ", ".join(job_label(j) for j in interrupted))
        )
        lines.append("")
    lines.append("Nothing new has been started. I'll ask you to confirm each one in turn.")
    try:
        await app.bot.send_message(chat_id=ALLOWED_CHAT_ID, text="\n".join(lines))
    except Exception as exc:
        logger.error("Could not announce the recovered queue: %s", exc)


async def on_shutdown(app):
    # Wait for the cancellation to actually land before saving. Cancelling is a
    # request, not an event: save_queue() straight afterwards would write the
    # queue as it was mid-flight, before _process had put the running job back.
    if _WORKER is not None:
        _WORKER.cancel()
        try:
            await _WORKER
        except BaseException:
            pass
    # The queue file is the handover to the next process.
    save_queue()
    logger.info("Saved %d pending job(s) to %s", len(QUEUE), QUEUE_FILE)


def main():
    token = get_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return
    logger.info(
        "Bot token loaded (id %s, secret withheld); allowed chat id %s",
        token.split(":", 1)[0] or "?", ALLOWED_CHAT_ID or "UNSET",
    )

    # NO concurrent_updates. Sequential update processing is now harmless — the
    # handlers acknowledge and return in under a second, and the long work
    # happens in the worker task — while enabling it would let two evaluations
    # overlap: double the API spend and two clients hammering CoinGecko.
    app = (
        Application.builder()
        .token(token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(on_confirm))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Committee bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
