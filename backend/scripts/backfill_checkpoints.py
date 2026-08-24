#!/usr/bin/env python3
"""Backfill the calibration ledger's missing 30-day checkpoints, and record a
one-off 67-day mark-to-market as prose.

Implements PROJECT_DECISIONS D5, both parts:

  (a) True 30-day checkpoints for the six usable records, using CoinGecko
      historical prices at the real target dates — 2026-07-11 for Aave,
      2026-07-18 for the Plasma/GEODNET/Ethena/Morpho/Pendle cohort — with the
      BTC benchmark fetched at those same dates. The two INSUFFICIENT_DATA rows
      are failed runs and are skipped.

  (b) A mark-to-market as of 2026-08-24 (67 days after the 18 June cohort's
      entry), written to ``outcome_notes`` as prose. It is deliberately NOT
      written into any dated column: 67 days is not a 30/90/180 horizon and a
      dated column would misrepresent it.

Safety properties, all non-negotiable:

  * ``--dry-run`` is the DEFAULT. Nothing is written without an explicit
    ``--commit``.
  * A dry run prints exactly what would be written, per record.
  * Every backfilled row is stamped in ``outcome_notes`` as a reconstruction,
    carrying the true observation date and the date the backfill ran. A
    reconstructed checkpoint must never be indistinguishable from a timely one.
  * Idempotent. A record that already has a price for the horizon is skipped
    unless ``--force``; the mark-to-market is skipped if its marker is already
    present in ``outcome_notes``.

``outcome_notes`` is owned by ``agent/persistence`` (docs/CONTRACTS.md §3.3) and
may not exist yet. The script checks for it up front and exits with a clear
message rather than crashing obscurely.

Usage (from /app inside the backend container):

    python3 -m scripts.backfill_checkpoints                # dry run, prints plan
    python3 -m scripts.backfill_checkpoints --commit       # actually writes
    python3 -m scripts.backfill_checkpoints --commit --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import text as sql_text

from app.knowledge.calibration import (
    HORIZON_COLUMNS,
    PriceLookup,
    compute_returns,
    fetch_price_on,
    observation_timestamp,
)

logger = logging.getLogger("backfill")

# --- constants ---------------------------------------------------------------

BACKFILL_HORIZON = 30
MARK_TO_MARKET_DATE = date(2026, 8, 24)
MARK_MARKER = "[MARK-TO-MARKET"
RECONSTRUCTION_MARKER = "[RECONSTRUCTED CHECKPOINT]"
SKIPPED_RECOMMENDATIONS = ("INSUFFICIENT_DATA",)

# The keyless free tier rate-limits at roughly four calls per 30 seconds, and
# signals it with an HTTP 200 body-level 429. Pace well inside that.
DEFAULT_MIN_INTERVAL_SECONDS = 20.0
DEFAULT_CACHE_FILE = "/tmp/aiic_backfill_price_cache.json"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISSING_COLUMN = 2


# --- data --------------------------------------------------------------------


@dataclass
class Record:
    id: str
    project_name: str
    ticker: str | None
    coingecko_id: str | None
    recommendation: str
    entry_price_usd: float | None
    btc_price_at_entry: float | None
    entry_captured_at: datetime | None
    existing_price: float | None  # price_{BACKFILL_HORIZON}d
    outcome_notes: str | None


@dataclass
class Plan:
    record: Record
    skip_reason: str | None = None
    # part (a)
    checkpoint: dict[str, Any] | None = None
    checkpoint_skip_reason: str | None = None
    # part (b)
    mark: dict[str, Any] | None = None
    mark_skip_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def writes_anything(self) -> bool:
        return self.checkpoint is not None or self.mark is not None


# --- repository seam ---------------------------------------------------------
# The script talks to storage only through this Protocol so the tests can drive
# the full plan/apply cycle (including idempotency across two runs) with an
# in-memory double and no database.


class Repo(Protocol):
    async def column_exists(self, column: str) -> bool: ...
    async def fetch_records(self) -> list[Record]: ...
    async def write_checkpoint(
        self,
        record_id: str,
        horizon_days: int,
        price: float,
        btc_price: float | None,
        return_pct: float,
        alpha_pct: float | None,
        checked_at: datetime,
        note: str,
    ) -> None: ...
    async def append_note(self, record_id: str, note: str) -> None: ...


class PostgresRepo:
    """The real repository, over ``app.database.async_session``."""

    async def column_exists(self, column: str) -> bool:
        from app.database import async_session
        from app.knowledge.calibration import column_exists as _column_exists

        async with async_session() as session:
            return await _column_exists(session, column)

    async def fetch_records(self) -> list[Record]:
        from app.database import async_session

        price_col = HORIZON_COLUMNS[BACKFILL_HORIZON]["price"]
        async with async_session() as session:
            result = await session.execute(
                sql_text(
                    f"""
                    SELECT id, project_name, ticker, coingecko_id, recommendation,
                           entry_price_usd, btc_price_at_entry, entry_captured_at,
                           {price_col}, outcome_notes
                    FROM calibration_records
                    ORDER BY entry_captured_at ASC, project_name ASC
                    """
                )
            )
            rows = result.fetchall()

        return [
            Record(
                id=str(row[0]),
                project_name=row[1],
                ticker=row[2],
                coingecko_id=row[3],
                recommendation=row[4],
                entry_price_usd=float(row[5]) if row[5] is not None else None,
                btc_price_at_entry=float(row[6]) if row[6] is not None else None,
                entry_captured_at=row[7],
                existing_price=float(row[8]) if row[8] is not None else None,
                outcome_notes=row[9],
            )
            for row in rows
        ]

    async def write_checkpoint(
        self,
        record_id: str,
        horizon_days: int,
        price: float,
        btc_price: float | None,
        return_pct: float,
        alpha_pct: float | None,
        checked_at: datetime,
        note: str,
    ) -> None:
        import uuid

        from app.database import async_session

        # Column names come from the module-level literal map in
        # knowledge/calibration.py, keyed by an int constant defined in this
        # file. Nothing user-supplied reaches the f-string.
        columns = HORIZON_COLUMNS[horizon_days]
        async with async_session() as session:
            await session.execute(
                sql_text(
                    f"""
                    UPDATE calibration_records
                    SET {columns["price"]} = :price,
                        {columns["btc_price"]} = :btc_price,
                        {columns["return_pct"]} = :return_pct,
                        {columns["alpha_pct"]} = :alpha_pct,
                        {columns["checked_at"]} = :checked_at,
                        outcome_notes = TRIM(BOTH E'\\n' FROM
                            COALESCE(outcome_notes, '') || E'\\n\\n' || :note)
                    WHERE id = :id
                    """
                ),
                {
                    "price": price,
                    "btc_price": btc_price,
                    "return_pct": return_pct,
                    "alpha_pct": alpha_pct,
                    "checked_at": checked_at,
                    "note": note,
                    "id": uuid.UUID(record_id),
                },
            )
            await session.commit()

    async def append_note(self, record_id: str, note: str) -> None:
        import uuid

        from app.database import async_session

        async with async_session() as session:
            await session.execute(
                sql_text(
                    r"""
                    UPDATE calibration_records
                    SET outcome_notes = TRIM(BOTH E'\n' FROM
                        COALESCE(outcome_notes, '') || E'\n\n' || :note)
                    WHERE id = :id
                    """
                ),
                {"note": note, "id": uuid.UUID(record_id)},
            )
            await session.commit()


# --- price fetching with a per-(coin, date) cache ----------------------------


class FetchFailed(RuntimeError):
    """A CoinGecko fetch did not complete (429 / timeout / HTTP error).

    Raised so the backfill aborts before writing anything. A half-written
    checkpoint set is worse than none: it leaves the ledger in a state nobody
    can distinguish from a complete one.
    """


class PriceCache:
    """Caches CoinGecko history lookups on disk, and paces the calls.

    Three reasons this exists:

    * **Pacing.** The keyless free tier rate-limits at roughly four calls per
      30 seconds, and answers an over-quota /history request with HTTP 200 and a
      body-level 429. Calls are spaced by ``min_interval_seconds``.
    * **Persistence.** The dry run and the subsequent ``--commit`` run need the
      same prices. Caching to disk means ``--commit`` re-fetches nothing, which
      halves the quota pressure and guarantees the committed numbers are exactly
      the ones a human reviewed.
    * **Honesty.** Only ``found`` and ``no_data`` are cached. A ``failed``
      lookup is never cached and never silently downgraded to "no data".
    """

    def __init__(
        self,
        fetch=fetch_price_on,
        *,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        cache_file: str | None = None,
    ):
        self._fetch = fetch
        self._cache: dict[tuple[str, date], PriceLookup] = {}
        self._min_interval = min_interval_seconds
        self._cache_file = pathlib.Path(cache_file) if cache_file else None
        self._last_call_at: float | None = None
        self.calls = 0
        self.cache_hits = 0
        self._load()

    # -- disk persistence --
    def _load(self) -> None:
        if not self._cache_file or not self._cache_file.exists():
            return
        try:
            raw = json.loads(self._cache_file.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable price cache %s: %s", self._cache_file, exc)
            return
        for key, value in raw.items():
            coin, _, day_str = key.partition("@")
            try:
                lookup = PriceLookup.from_dict(value)
            except (KeyError, TypeError):
                continue
            if lookup.failed:
                continue  # never trust a cached failure
            self._cache[(coin, date.fromisoformat(day_str))] = lookup

    def _save(self) -> None:
        if not self._cache_file:
            return
        raw = {
            f"{coin}@{day.isoformat()}": lookup.as_dict()
            for (coin, day), lookup in self._cache.items()
            if not lookup.failed
        }
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(json.dumps(raw, indent=2, sort_keys=True))
        except OSError as exc:
            logger.warning("Could not write price cache %s: %s", self._cache_file, exc)

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        if self._last_call_at is not None:
            elapsed = time.monotonic() - self._last_call_at
            wait = self._min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_call_at = time.monotonic()

    async def lookup(self, coingecko_id: str, day: date) -> PriceLookup:
        """Return the lookup for (coin, day). Raises FetchFailed on a failure."""
        key = (coingecko_id, day)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]

        await self._throttle()
        self.calls += 1
        result = await self._fetch(coingecko_id, day)

        if result.failed:
            # Not cached, and not returned as "no data". Abort the run.
            raise FetchFailed(
                f"CoinGecko fetch failed for {coingecko_id!r} on "
                f"{day.isoformat()}: {result.detail}"
            )

        self._cache[key] = result
        self._save()
        return result

    async def price_on(self, coingecko_id: str, day: date) -> float | None:
        """The price, or None when CoinGecko genuinely has no data for that date."""
        return (await self.lookup(coingecko_id, day)).price

    @property
    def cache_path(self) -> str:
        return str(self._cache_file) if self._cache_file else ""

    @property
    def min_interval(self) -> float:
        return self._min_interval


# --- note construction -------------------------------------------------------


def backfill_note(
    *,
    project_name: str,
    horizon_days: int,
    entry_day: date,
    target_date: date,
    performed_on: date,
    coingecko_id: str,
    price: float,
    btc_price: float | None,
    return_pct: float,
    alpha_pct: float | None,
) -> str:
    alpha_str = f"{alpha_pct:+.2f}pp" if alpha_pct is not None else "n/a"
    btc_str = f"{btc_price}" if btc_price is not None else "unavailable"
    days_late = (performed_on - target_date).days
    return (
        f"{RECONSTRUCTION_MARKER} {horizon_days}d checkpoint for {project_name} was NOT "
        f"captured on the day. True observation date {target_date.isoformat()} "
        f"(entry {entry_day.isoformat()} + {horizon_days}d). Reconstructed on "
        f"{performed_on.isoformat()}, {days_late} days late, by "
        f"backend/scripts/backfill_checkpoints.py from CoinGecko "
        f"/coins/{coingecko_id}/history (asset and BTC both as of "
        f"{target_date.isoformat()}). price_{horizon_days}d={price} USD, "
        f"btc_price_{horizon_days}d={btc_str} USD, "
        f"return_{horizon_days}d={return_pct:+.2f}%, "
        f"alpha_vs_btc_{horizon_days}d={alpha_str} (alpha = simple difference "
        f"return_pct - btc_return_pct, not a ratio). This row is a "
        f"RECONSTRUCTION, not a timely observation."
    )


def mark_to_market_note(
    *,
    project_name: str,
    entry_day: date,
    as_of: date,
    performed_on: date,
    coingecko_id: str,
    entry_price: float,
    price: float,
    btc_entry: float | None,
    btc_price: float | None,
    return_pct: float,
    alpha_pct: float | None,
) -> str:
    elapsed = (as_of - entry_day).days
    alpha_str = f"{alpha_pct:+.2f}pp" if alpha_pct is not None else "n/a"
    btc_str = f"{btc_price}" if btc_price is not None else "unavailable"
    btc_entry_str = f"{btc_entry}" if btc_entry is not None else "unavailable"
    return (
        f"{MARK_MARKER} {as_of.isoformat()}] {elapsed}-day mark-to-market for "
        f"{project_name}, {elapsed} days after entry on {entry_day.isoformat()}. "
        f"Entry {entry_price} USD -> {price} USD on {as_of.isoformat()} "
        f"(CoinGecko /coins/{coingecko_id}/history). "
        f"BTC {btc_entry_str} -> {btc_str} over the same window. "
        f"return={return_pct:+.2f}%, alpha={alpha_str} (simple difference "
        f"return_pct - btc_return_pct, not a ratio). "
        f"Recorded as prose on {performed_on.isoformat()} for the performance "
        f"retrospective. Deliberately NOT written to price_30d/90d/180d or any "
        f"other dated column: {elapsed} days is not a 30/90/180 horizon and a "
        f"dated column would misrepresent it."
    )


# --- planning ----------------------------------------------------------------


def _entry_day(record: Record) -> date | None:
    value = record.entry_captured_at
    if value is None:
        return None
    return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()


async def plan_record(
    record: Record,
    cache: PriceCache,
    *,
    horizon_days: int,
    mark_as_of: date,
    performed_on: date,
    force: bool,
    do_checkpoint: bool,
    do_mark: bool,
) -> Plan:
    plan = Plan(record=record)

    if record.recommendation in SKIPPED_RECOMMENDATIONS:
        plan.skip_reason = (
            f"recommendation is {record.recommendation} — this is a failed committee "
            f"run, not a real call. Nothing to calibrate."
        )
        return plan

    entry_day = _entry_day(record)
    if entry_day is None:
        plan.skip_reason = "record has no entry_captured_at"
        return plan
    if record.entry_price_usd is None:
        plan.skip_reason = "record has no entry_price_usd"
        return plan
    if not record.coingecko_id:
        plan.skip_reason = "record has no coingecko_id"
        return plan

    target_date = entry_day + timedelta(days=horizon_days)

    # --- part (a): the true horizon-N checkpoint ---
    if not do_checkpoint:
        plan.checkpoint_skip_reason = "checkpoint backfill disabled (--no-checkpoint)"
    elif record.existing_price is not None and not force:
        plan.checkpoint_skip_reason = (
            f"already has {HORIZON_COLUMNS[horizon_days]['price']}="
            f"{record.existing_price} — skipping (use --force to overwrite)"
        )
    elif target_date > performed_on:
        plan.checkpoint_skip_reason = (
            f"target date {target_date.isoformat()} is in the future — refusing"
        )
    else:
        price = await cache.price_on(record.coingecko_id, target_date)
        if price is None:
            plan.checkpoint_skip_reason = (
                f"CoinGecko returned no market_data for {record.coingecko_id!r} "
                f"on {target_date.isoformat()}"
            )
        else:
            btc_price = await cache.price_on("bitcoin", target_date)
            return_pct, alpha_pct = compute_returns(
                record.entry_price_usd, price, record.btc_price_at_entry, btc_price
            )
            plan.checkpoint = {
                "horizon_days": horizon_days,
                "target_date": target_date,
                "checked_at": observation_timestamp(target_date),
                "price": price,
                "btc_price": btc_price,
                "return_pct": round(return_pct, 2),
                "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
                "note": backfill_note(
                    project_name=record.project_name,
                    horizon_days=horizon_days,
                    entry_day=entry_day,
                    target_date=target_date,
                    performed_on=performed_on,
                    coingecko_id=record.coingecko_id,
                    price=price,
                    btc_price=btc_price,
                    return_pct=round(return_pct, 2),
                    alpha_pct=round(alpha_pct, 2) if alpha_pct is not None else None,
                ),
            }

    # --- part (b): the mark-to-market, prose only ---
    if not do_mark:
        plan.mark_skip_reason = "mark-to-market disabled (--no-mark)"
    elif MARK_MARKER in (record.outcome_notes or "") and not force:
        plan.mark_skip_reason = (
            "outcome_notes already contains a mark-to-market — skipping "
            "(use --force to add another)"
        )
    elif mark_as_of > performed_on:
        plan.mark_skip_reason = f"mark date {mark_as_of.isoformat()} is in the future — refusing"
    else:
        price = await cache.price_on(record.coingecko_id, mark_as_of)
        if price is None:
            plan.mark_skip_reason = (
                f"CoinGecko returned no market_data for {record.coingecko_id!r} "
                f"on {mark_as_of.isoformat()}"
            )
        else:
            btc_price = await cache.price_on("bitcoin", mark_as_of)
            return_pct, alpha_pct = compute_returns(
                record.entry_price_usd, price, record.btc_price_at_entry, btc_price
            )
            plan.mark = {
                "as_of": mark_as_of,
                "elapsed_days": (mark_as_of - entry_day).days,
                "price": price,
                "btc_price": btc_price,
                "return_pct": round(return_pct, 2),
                "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
                "note": mark_to_market_note(
                    project_name=record.project_name,
                    entry_day=entry_day,
                    as_of=mark_as_of,
                    performed_on=performed_on,
                    coingecko_id=record.coingecko_id,
                    entry_price=record.entry_price_usd,
                    price=price,
                    btc_entry=record.btc_price_at_entry,
                    btc_price=btc_price,
                    return_pct=round(return_pct, 2),
                    alpha_pct=round(alpha_pct, 2) if alpha_pct is not None else None,
                ),
            }

    return plan


# --- rendering ---------------------------------------------------------------


def render_plan(plan: Plan, out=sys.stdout) -> None:
    r = plan.record
    entry_day = _entry_day(r)
    entry_str = entry_day.isoformat() if entry_day else "unknown"
    print(f"\n{'-' * 78}", file=out)
    print(
        f"{r.project_name} ({r.ticker or '?'})  rec={r.recommendation}  "
        f"entry={entry_str}  id={r.id}",
        file=out,
    )
    print(f"  coingecko_id={r.coingecko_id}  entry_price={r.entry_price_usd} USD  "
          f"btc_at_entry={r.btc_price_at_entry}", file=out)

    if plan.skip_reason:
        print(f"  SKIP RECORD: {plan.skip_reason}", file=out)
        return

    if plan.checkpoint:
        c = plan.checkpoint
        cols = HORIZON_COLUMNS[c["horizon_days"]]
        print(f"  WOULD WRITE ({c['horizon_days']}d checkpoint, "
              f"observation date {c['target_date'].isoformat()}):", file=out)
        print(f"      {cols['price']:<24} = {c['price']}", file=out)
        print(f"      {cols['btc_price']:<24} = {c['btc_price']}", file=out)
        print(f"      {cols['return_pct']:<24} = {c['return_pct']}", file=out)
        print(f"      {cols['alpha_pct']:<24} = {c['alpha_pct']}", file=out)
        print(f"      {cols['checked_at']:<24} = {c['checked_at'].isoformat()}   "
              f"<- TRUE observation date, not now()", file=out)
        print(f"      outcome_notes            += {c['note']}", file=out)
    else:
        print(f"  no checkpoint write: {plan.checkpoint_skip_reason}", file=out)

    if plan.mark:
        m = plan.mark
        print(f"  WOULD APPEND ({m['elapsed_days']}-day mark-to-market as of "
              f"{m['as_of'].isoformat()}, prose only, no dated column):", file=out)
        print(f"      price={m['price']}  btc={m['btc_price']}  "
              f"return={m['return_pct']}%  alpha={m['alpha_pct']}pp", file=out)
        print(f"      outcome_notes            += {m['note']}", file=out)
    else:
        print(f"  no mark-to-market write: {plan.mark_skip_reason}", file=out)


# --- driver ------------------------------------------------------------------


async def run(
    repo: Repo,
    *,
    commit: bool,
    force: bool,
    horizon_days: int = BACKFILL_HORIZON,
    mark_as_of: date = MARK_TO_MARKET_DATE,
    performed_on: date | None = None,
    do_checkpoint: bool = True,
    do_mark: bool = True,
    cache: PriceCache | None = None,
    out=sys.stdout,
) -> int:
    performed_on = performed_on or datetime.now(timezone.utc).date()
    cache = cache or PriceCache()

    if not await repo.column_exists("outcome_notes"):
        print(
            "\nFATAL: column calibration_records.outcome_notes does not exist.\n"
            "\n"
            "Every backfilled checkpoint must be stamped as a reconstruction, and\n"
            "that stamp lives in outcome_notes. Without the column this script\n"
            "would write checkpoints that are indistinguishable from timely ones,\n"
            "so it refuses to run at all.\n"
            "\n"
            "outcome_notes is owned by agent/persistence (docs/CONTRACTS.md §3.3)\n"
            "and must ship as a migration, not just in init.sql. Ask the\n"
            "orchestrator for it; do not add the column from this branch.\n",
            file=out,
        )
        return EXIT_MISSING_COLUMN

    records = await repo.fetch_records()

    mode = "COMMIT (writes will be made)" if commit else "DRY RUN (default — nothing will be written)"
    print(f"{'=' * 78}", file=out)
    print("calibration checkpoint backfill — PROJECT_DECISIONS D5", file=out)
    print(f"{'=' * 78}", file=out)
    print(f"mode              : {mode}", file=out)
    print(f"force             : {force}", file=out)
    print(f"target database   : {_describe_database()}", file=out)
    print(f"backfill horizon  : {horizon_days}d (part a — true historical checkpoint)", file=out)
    print(f"mark-to-market    : {mark_as_of.isoformat()} (part b — outcome_notes prose only)", file=out)
    print(f"performed on      : {performed_on.isoformat()}", file=out)
    print(f"price cache       : {cache.cache_path or '(disabled)'}", file=out)
    print(f"call spacing      : {cache.min_interval}s between CoinGecko calls", file=out)
    print(f"records in ledger : {len(records)}", file=out)

    plans: list[Plan] = []
    try:
        for record in records:
            plans.append(
                await plan_record(
                    record,
                    cache,
                    horizon_days=horizon_days,
                    mark_as_of=mark_as_of,
                    performed_on=performed_on,
                    force=force,
                    do_checkpoint=do_checkpoint,
                    do_mark=do_mark,
                )
            )
            render_plan(plans[-1], out=out)
    except FetchFailed as exc:
        # Every write happens after planning completes, so aborting here
        # guarantees nothing was written. A partially-backfilled ledger is worse
        # than an empty one because nobody can tell the two apart afterwards.
        print(
            f"\n{'=' * 78}\n"
            f"ABORTED — a CoinGecko fetch failed. NOTHING WAS WRITTEN.\n"
            f"{'=' * 78}\n"
            f"  {exc}\n"
            f"\n"
            f"  A failed fetch is not a data gap. Rather than record it as one,\n"
            f"  the run is abandoned. Prices already fetched are cached in\n"
            f"  {cache.cache_path or '(no cache file)'}, so a re-run resumes\n"
            f"  instead of starting over. Wait a minute for the rate limit\n"
            f"  window to clear, then run again.\n",
            file=out,
        )
        return EXIT_FAILED

    skipped = [p for p in plans if p.skip_reason]
    checkpoint_writes = [p for p in plans if p.checkpoint]
    mark_writes = [p for p in plans if p.mark]

    print(f"\n{'=' * 78}", file=out)
    print("SUMMARY", file=out)
    print(f"{'=' * 78}", file=out)
    print(f"  records examined        : {len(plans)}", file=out)
    print(f"  records skipped entirely: {len(skipped)}", file=out)
    for p in skipped:
        print(f"      - {p.record.project_name} ({p.record.recommendation}): {p.skip_reason}", file=out)
    print(f"  {horizon_days}d checkpoints to write : {len(checkpoint_writes)}", file=out)
    print(f"  mark-to-market notes    : {len(mark_writes)}", file=out)
    print(f"  CoinGecko history calls : {cache.calls} ({cache.cache_hits} served from cache)", file=out)

    if not commit:
        print(
            "\nDRY RUN — nothing was written. Review the above, then re-run with\n"
            "--commit to apply.\n",
            file=out,
        )
        return EXIT_OK

    if not checkpoint_writes and not mark_writes:
        print("\nNothing to write. Ledger already up to date.\n", file=out)
        return EXIT_OK

    print("\nCOMMITTING...", file=out)
    for p in plans:
        if p.checkpoint:
            c = p.checkpoint
            await repo.write_checkpoint(
                record_id=p.record.id,
                horizon_days=c["horizon_days"],
                price=c["price"],
                btc_price=c["btc_price"],
                return_pct=c["return_pct"],
                alpha_pct=c["alpha_pct"],
                checked_at=c["checked_at"],
                note=c["note"],
            )
            print(f"  wrote {c['horizon_days']}d checkpoint for {p.record.project_name}", file=out)
        if p.mark:
            await repo.append_note(p.record.id, p.mark["note"])
            print(f"  appended mark-to-market note for {p.record.project_name}", file=out)
    print("done.\n", file=out)
    return EXIT_OK


def _describe_database() -> str:
    """Host and database name only — never the password."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            from app.config import get_settings

            url = get_settings().database_url
        except Exception:
            return "unknown"
    tail = url.rsplit("@", 1)[-1]
    return tail or "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill 30d calibration checkpoints from true historical prices and "
            "record a 67-day mark-to-market as prose. Dry run by default."
        )
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually write. Without this flag the script is a dry run and writes nothing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite checkpoints that already have a price, and append a "
            "mark-to-market note even if one is already present."
        ),
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=BACKFILL_HORIZON,
        choices=sorted(HORIZON_COLUMNS),
        help=f"Checkpoint horizon to backfill (default {BACKFILL_HORIZON}).",
    )
    parser.add_argument(
        "--mark-as-of",
        type=date.fromisoformat,
        default=MARK_TO_MARKET_DATE,
        metavar="YYYY-MM-DD",
        help=f"Mark-to-market date (default {MARK_TO_MARKET_DATE.isoformat()}).",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Override 'today' — the date the backfill is recorded as having been performed.",
    )
    parser.add_argument(
        "--cache-file",
        default=DEFAULT_CACHE_FILE,
        metavar="PATH",
        help=(
            "Where to persist fetched prices so --commit reuses the dry run's "
            f"numbers instead of re-fetching (default {DEFAULT_CACHE_FILE}). "
            "Pass an empty string to disable."
        ),
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_MIN_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=(
            "Seconds between CoinGecko calls (default "
            f"{DEFAULT_MIN_INTERVAL_SECONDS}). The free tier rate-limits at "
            "roughly four calls per 30 seconds."
        ),
    )
    parser.add_argument("--no-checkpoint", action="store_true", help="Skip part (a).")
    parser.add_argument("--no-mark", action="store_true", help="Skip part (b).")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return asyncio.run(
        run(
            PostgresRepo(),
            commit=args.commit,
            force=args.force,
            horizon_days=args.horizon,
            mark_as_of=args.mark_as_of,
            performed_on=args.today,
            do_checkpoint=not args.no_checkpoint,
            do_mark=not args.no_mark,
            cache=PriceCache(
                min_interval_seconds=args.min_interval,
                cache_file=args.cache_file or None,
            ),
        )
    )


if __name__ == "__main__":
    sys.exit(main())
