"""Calibration tracking for evaluating recommendation quality over time.

Checkpoints are **date anchored**. A checkpoint for horizon N is the price of the
asset on ``entry_captured_at + N days`` — never today's spot price. Running a
checkpoint late must still produce the price for the original target date, and
``checked_{N}d_at`` must record the *true observation date*, not the moment the
job happened to run. See ``docs/CONTRACTS.md`` §3.2.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text as sql_text

from app.database import async_session

# CoinGecko courtesy behaviour (exponential backoff on 429 plus the optional
# demo API key header) already exists in the tools layer and is reused verbatim
# here rather than duplicated. 429 storms are a documented failure mode of this
# project (handoff §9.5), so this module must never issue a bare httpx.get
# against CoinGecko.
# _get_with_backoff applies _headers() internally, so the API-key header comes
# along for free.
from app.tools.coingecko import BASE_URL as COINGECKO, RETRY_DELAYS_SECONDS, _get_with_backoff

logger = logging.getLogger(__name__)

__all__ = [
    "COINGECKO",
    "HISTORY_RETRY_DELAYS_SECONDS",
    "PriceLookup",
    "RETRY_DELAYS_SECONDS",
    "body_rate_limited",
    "VALID_HORIZONS",
    "HORIZON_COLUMNS",
    "coingecko_date",
    "column_exists",
    "compute_checkpoint",
    "compute_returns",
    "fetch_price_on",
    "get_scorecard",
    "observation_timestamp",
    "reconstruction_note",
    "record_calibration",
    "resolve_target_date",
    "update_checkpoint",
]

VALID_HORIZONS: tuple[int, ...] = (30, 90, 180)

# The free tier answers an over-quota /history call with HTTP 200 and a
# body-level 429, which _get_with_backoff cannot see. The observed window is
# roughly 30 seconds, so this ladder is much longer than the HTTP-level one.
HISTORY_RETRY_DELAYS_SECONDS: tuple[int, ...] = (20, 40, 60)

# Column names are interpolated into SQL with an f-string further down. They are
# built here, at import time, from the literal VALID_HORIZONS tuple — so every
# string that reaches the f-string is fixed by this module and can never
# originate from a request path, a database row, or any other caller input.
HORIZON_COLUMNS: dict[int, dict[str, str]] = {
    horizon: {
        "price": f"price_{horizon}d",
        "checked_at": f"checked_{horizon}d_at",
        "btc_price": f"btc_price_{horizon}d",
        "return_pct": f"return_{horizon}d_pct",
        "alpha_pct": f"alpha_vs_btc_{horizon}d_pct",
    }
    for horizon in VALID_HORIZONS
}

# CoinGecko coin ids are lowercase alphanumerics with hyphens. The id comes out
# of the database and then goes into a URL *path*, so it is validated rather
# than trusted.
_COIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _valid_horizon(horizon_days: Any) -> bool:
    """True only for a real ``int`` equal to 30, 90 or 180.

    ``bool`` is rejected explicitly (``True == 1``) and non-ints are rejected
    because ``30.0 in (30, 90, 180)`` is True but would render ``price_30.0d``.
    """
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int):
        return False
    return horizon_days in VALID_HORIZONS


def coingecko_date(day: date) -> str:
    """Format a date the way CoinGecko's /history endpoint wants it: DD-MM-YYYY.

    Note the ordering — day first, then month. ``2026-07-11`` becomes
    ``11-07-2026``, not ``07-11-2026``.
    """
    return f"{day.day:02d}-{day.month:02d}-{day.year:04d}"


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return None


def observation_timestamp(day: date) -> datetime:
    """The timestamptz written into ``checked_{N}d_at``: UTC midnight of the
    observation date. CoinGecko /history is a daily snapshot, so midnight UTC is
    the honest instant to attach to it."""
    return datetime.combine(day, time(0, 0), tzinfo=timezone.utc)


def resolve_target_date(
    entry_captured_at: Any,
    horizon_days: int,
    as_of: date | None = None,
) -> date | None:
    """The date a checkpoint observes.

    An explicit ``as_of`` wins. Otherwise the date is derived from the record's
    own ``entry_captured_at`` plus the horizon — never from ``now()``. Returns
    ``None`` when neither is available.
    """
    if as_of is not None:
        return as_of
    entry_day = _as_date(entry_captured_at)
    if entry_day is None:
        return None
    return entry_day + timedelta(days=horizon_days)


def compute_returns(
    entry_price: Any,
    observed_price: Any,
    btc_entry: Any,
    btc_observed: Any,
) -> tuple[float, float | None]:
    """Return ``(return_pct, alpha_pct)``.

    ``alpha_pct`` is the **simple arithmetic difference**
    ``return_pct - btc_return_pct``, expressed in percentage points. It is NOT a
    ratio, not a beta-adjusted excess return, and not a regression alpha. A
    token that fell 10% while BTC fell 4% has an alpha of -6 percentage points.
    The handoff (§6.3) calls this definition out explicitly; it is preserved
    unchanged here so existing and future ledger rows stay comparable.
    """
    entry = float(entry_price)
    observed = float(observed_price)
    return_pct = ((observed - entry) / entry) * 100

    alpha_pct: float | None = None
    if btc_observed is not None and btc_entry is not None:
        btc_return_pct = ((float(btc_observed) - float(btc_entry)) / float(btc_entry)) * 100
        alpha_pct = return_pct - btc_return_pct
    return return_pct, alpha_pct


async def _fetch_price(coingecko_id: str) -> dict[str, Any]:
    """Spot price. Used only at entry capture — never for a checkpoint."""
    if not coingecko_id:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await _get_with_backoff(
                client,
                "simple/price",
                params={
                    "ids": coingecko_id,
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                },
            )
            if response is None:
                logger.warning("Calibration price fetch rate-limited for %s", coingecko_id)
                return {}
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Calibration price fetch failed for %s: %s", coingecko_id, exc)
        return {}

    if coingecko_id not in data:
        return {}
    return {
        "price": data[coingecko_id].get("usd"),
        "market_cap": data[coingecko_id].get("usd_market_cap"),
    }


async def _fetch_benchmarks() -> dict[str, float | None]:
    """Spot benchmarks. Used only at entry capture — never for a checkpoint."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await _get_with_backoff(
                client,
                "simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"},
            )
            if response is None:
                logger.warning("Calibration benchmark fetch rate-limited")
                return {"btc": None, "eth": None}
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Calibration benchmark fetch failed: %s", exc)
        return {"btc": None, "eth": None}

    return {
        "btc": data.get("bitcoin", {}).get("usd"),
        "eth": data.get("ethereum", {}).get("usd"),
    }


class PriceLookup:
    """The outcome of one historical price lookup.

    Three outcomes, deliberately kept distinct:

    * ``found``    - a real price for that date.
    * ``no_data``  - CoinGecko answered, and genuinely has no market data for
                     that date (the coin predates it).
    * ``failed``   - the fetch did not complete: rate limited, timed out, or an
                     HTTP error.

    Collapsing ``failed`` into ``no_data`` is how a rate limit gets silently
    recorded as a genuine data gap. On a backfill against the only calibration
    ledger that exists, that is a corruption bug, so callers must branch on
    ``status`` and abort on ``failed`` rather than treating it as "nothing to
    write".
    """

    __slots__ = ("status", "price", "market_cap", "detail")

    def __init__(
        self,
        status: str,
        price: float | None = None,
        market_cap: float | None = None,
        detail: str = "",
    ) -> None:
        if status == "found" and price is None:
            # The invariant every caller relies on. Enforced here rather than
            # left as a convention, because a "found" with no price would flow
            # straight into float() and raise TypeError several frames away.
            raise ValueError("PriceLookup('found') requires a price")
        self.status = status
        self.price = price
        self.market_cap = market_cap
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status == "found"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PriceLookup(status={self.status!r}, price={self.price!r}, detail={self.detail!r})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "price": self.price,
            "market_cap": self.market_cap,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceLookup":
        return cls(
            status=data["status"],
            price=data.get("price"),
            market_cap=data.get("market_cap"),
            detail=data.get("detail", ""),
        )


def body_rate_limited(data: Any) -> bool:
    """True when a HTTP 200 body is actually a rate-limit error.

    CoinGecko's free tier answers an over-quota /history request with HTTP 200
    and a body of ``{"status": {"error_code": 429, "error_message": ...}}`` —
    no ``market_data`` key at all. Checking only for ``market_data`` would read
    that as "the coin did not exist on that date".
    """
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if not isinstance(status, dict):
        return False
    return status.get("error_code") == 429


async def fetch_price_on(coingecko_id: str, day: date) -> PriceLookup:
    """Price of ``coingecko_id`` **as of** ``day``, via /coins/{id}/history.

    Returns a :class:`PriceLookup`. See that class for why ``no_data`` and
    ``failed`` must not be collapsed into one another.
    """
    if not coingecko_id:
        return PriceLookup("no_data", detail="empty coingecko id")
    if not _COIN_ID_RE.match(coingecko_id):
        logger.warning("Refusing malformed coingecko id in URL path: %r", coingecko_id)
        return PriceLookup("failed", detail=f"malformed coingecko id {coingecko_id!r}")

    params = {"date": coingecko_date(day), "localization": "false"}
    data: Any = None

    # _get_with_backoff retries an HTTP-level 429. The body-level 429 arrives as
    # HTTP 200, so it needs its own retry ladder on top. The free-tier window is
    # roughly 30 seconds, so these delays are deliberately long.
    for attempt, delay in enumerate((0, *HISTORY_RETRY_DELAYS_SECONDS), start=1):
        if delay:
            logger.warning(
                "CoinGecko body-level 429 for %s on %s; sleeping %ss (attempt %s)",
                coingecko_id,
                day,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await _get_with_backoff(
                    client, f"coins/{coingecko_id}/history", params=params
                )
                if response is None:
                    return PriceLookup(
                        "failed",
                        detail=f"HTTP 429 persisted after {len(RETRY_DELAYS_SECONDS)} retries",
                    )
                if response.status_code == 404:
                    logger.warning("CoinGecko has no coin %r", coingecko_id)
                    return PriceLookup("no_data", detail=f"unknown coin {coingecko_id!r}")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "Historical price fetch failed for %s on %s: %s", coingecko_id, day, exc
            )
            return PriceLookup("failed", detail=f"{type(exc).__name__}: {exc}")
        except ValueError as exc:  # malformed JSON
            return PriceLookup("failed", detail=f"unparseable response: {exc}")

        if not body_rate_limited(data):
            break
    else:
        return PriceLookup(
            "failed",
            detail="CoinGecko body-level 429 persisted after all retries",
        )

    market_data = data.get("market_data") if isinstance(data, dict) else None
    if not market_data:
        # Genuine gap: CoinGecko answered and simply has no market data for this
        # date. We only get here once a body-level 429 has been ruled out.
        logger.info("CoinGecko has no market_data for %s on %s", coingecko_id, day)
        return PriceLookup(
            "no_data", detail=f"no market_data for {coingecko_id!r} on {day.isoformat()}"
        )

    price = market_data.get("current_price", {}).get("usd")
    if price is None:
        return PriceLookup(
            "no_data", detail=f"no USD price for {coingecko_id!r} on {day.isoformat()}"
        )
    return PriceLookup(
        "found", price=price, market_cap=market_data.get("market_cap", {}).get("usd")
    )


async def column_exists(session: Any, column_name: str) -> bool:
    """Whether ``calibration_records`` has ``column_name``."""
    result = await session.execute(
        sql_text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'calibration_records' AND column_name = :col
            """
        ),
        {"col": column_name},
    )
    return result.fetchone() is not None


async def record_calibration(
    evaluation_id: str | None,
    project_name: str,
    ticker: str,
    coingecko_id: str,
    category: str,
    recommendation: str,
    overall_score: float | None,
    chair_confidence: str,
    vetoed: bool,
) -> str | None:
    """Capture an entry snapshot. Signature is FROZEN by docs/CONTRACTS.md §3.1."""
    price_data = await _fetch_price(coingecko_id)
    benchmarks = await _fetch_benchmarks()
    now = datetime.now(timezone.utc)

    try:
        async with async_session() as session:
            result = await session.execute(
                sql_text(
                    """
                    INSERT INTO calibration_records (
                        evaluation_id, project_name, ticker, coingecko_id, category,
                        recommendation, overall_score, chair_confidence, vetoed,
                        entry_price_usd, entry_market_cap_usd, entry_captured_at,
                        btc_price_at_entry, eth_price_at_entry
                    ) VALUES (
                        :evaluation_id, :project_name, :ticker, :coingecko_id, :category,
                        :recommendation, :overall_score, :chair_confidence, :vetoed,
                        :entry_price, :entry_mcap, :captured_at,
                        :btc, :eth
                    )
                    RETURNING id
                    """
                ),
                {
                    "evaluation_id": uuid.UUID(evaluation_id) if evaluation_id else None,
                    "project_name": project_name,
                    "ticker": ticker,
                    "coingecko_id": coingecko_id,
                    "category": category,
                    "recommendation": recommendation,
                    "overall_score": overall_score,
                    "chair_confidence": chair_confidence,
                    "vetoed": vetoed,
                    "entry_price": price_data.get("price"),
                    "entry_mcap": price_data.get("market_cap"),
                    "captured_at": now,
                    "btc": benchmarks.get("btc"),
                    "eth": benchmarks.get("eth"),
                },
            )
            record_id = result.scalar()
            await session.commit()
    except Exception as exc:
        logger.warning("Calibration record failed (non-fatal): %s", exc)
        return None

    logger.info(
        "Calibration recorded for %s: recommendation=%s entry_price=%s",
        project_name,
        recommendation,
        price_data.get("price"),
    )
    return str(record_id)


async def compute_checkpoint(
    record_id: str,
    horizon_days: int,
    as_of: date | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Compute a checkpoint **without writing anything**.

    Fetches the asset price and the BTC benchmark at the *same* target date and
    returns everything a caller needs in order to write the row — or an
    ``error`` key. ``update_checkpoint`` and the backfill script both go through
    this, so a dry-run and a real run compute identically.
    """
    if not _valid_horizon(horizon_days):
        return {"error": "horizon must be 30, 90, or 180"}

    today = today or datetime.now(timezone.utc).date()

    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT coingecko_id, entry_price_usd, btc_price_at_entry,
                       entry_captured_at, project_name, recommendation
                FROM calibration_records
                WHERE id = :id
                """
            ),
            {"id": uuid.UUID(record_id)},
        )
        row = result.fetchone()

    if not row:
        return {"error": "record not found"}

    coingecko_id, entry_price, btc_entry, entry_captured_at, project_name, recommendation = row

    target_date = resolve_target_date(entry_captured_at, horizon_days, as_of)
    if target_date is None:
        return {
            "error": (
                "cannot determine target date: record has no entry_captured_at "
                "and no as_of was supplied"
            )
        }
    if target_date > today:
        return {
            "error": (
                f"target date {target_date.isoformat()} is in the future "
                f"(today is {today.isoformat()}); nothing written"
            )
        }
    if entry_price is None:
        # Reachable: the 11 June Aave INSUFFICIENT_DATA row has a NULL
        # entry_price_usd, and the HTTP endpoint will happily be pointed at it.
        # Without this guard the float() below raises TypeError.
        return {"error": "record has no entry_price_usd"}
    entry_price_value = float(entry_price)

    lookup = await fetch_price_on(coingecko_id, target_date)
    if lookup.failed:
        # A failed fetch is NOT a data gap. Reporting it as one would let a rate
        # limit masquerade as "this coin did not exist yet".
        return {
            "error": (
                f"price fetch FAILED for {coingecko_id!r} on "
                f"{target_date.isoformat()}: {lookup.detail}. Nothing written — "
                f"retry later."
            ),
            "fetch_failed": True,
        }
    observed_price = lookup.price
    if not lookup.ok or observed_price is None:
        return {
            "error": (
                f"no historical price for {coingecko_id!r} on "
                f"{target_date.isoformat()} (CoinGecko returned no market_data)"
            ),
            "fetch_failed": False,
        }

    # The BTC benchmark MUST come from the same date as the asset price.
    # Comparing a historical asset price against BTC spot would be a worse bug
    # than the spot-vs-spot one this replaces, not a better one.
    btc_lookup = await fetch_price_on("bitcoin", target_date)
    if btc_lookup.failed:
        # Writing the asset price with a NULL alpha because BTC was rate limited
        # would quietly produce an unbenchmarked row. Refuse the whole thing.
        return {
            "error": (
                f"BTC benchmark fetch FAILED for {target_date.isoformat()}: "
                f"{btc_lookup.detail}. Nothing written — retry later."
            ),
            "fetch_failed": True,
        }
    btc_observed = btc_lookup.price
    if btc_observed is None and btc_entry is not None:
        logger.warning(
            "No BTC price for %s; alpha will be NULL for record %s",
            target_date,
            record_id,
        )

    return_pct, alpha_pct = compute_returns(
        entry_price_value, observed_price, btc_entry, btc_observed
    )

    return {
        "record_id": record_id,
        "project_name": project_name,
        "recommendation": recommendation,
        "coingecko_id": coingecko_id,
        "horizon_days": horizon_days,
        "entry_captured_at": entry_captured_at,
        "target_date": target_date,
        "observed_at": observation_timestamp(target_date),
        "is_reconstruction": target_date < today,
        "days_late": (today - target_date).days,
        "entry_price": entry_price_value,
        "observed_price": float(observed_price),
        "btc_price_at_entry": float(btc_entry) if btc_entry is not None else None,
        "btc_price_observed": float(btc_observed) if btc_observed is not None else None,
        "return_pct": round(return_pct, 2),
        "alpha_vs_btc_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
    }


def reconstruction_note(computed: dict[str, Any], performed_on: date) -> str:
    """The provenance stamp for a checkpoint that was not captured on the day.

    A reconstructed checkpoint must never be indistinguishable from a timely
    one, so the stamp carries both the true observation date and the date on
    which the reconstruction was performed.
    """
    entry_day = _as_date(computed.get("entry_captured_at"))
    entry_str = entry_day.isoformat() if entry_day else "unknown"
    alpha = computed["alpha_vs_btc_pct"]
    alpha_str = f"{alpha:+.2f}pp" if alpha is not None else "n/a"
    return (
        f"[RECONSTRUCTED CHECKPOINT] {computed['horizon_days']}d checkpoint for "
        f"{computed.get('project_name') or 'unknown'} was NOT captured on the day. "
        f"True observation date {computed['target_date'].isoformat()} "
        f"(entry {entry_str} + {computed['horizon_days']}d). "
        f"Reconstructed on {performed_on.isoformat()}, {computed['days_late']} days late, "
        f"from CoinGecko /coins/{computed['coingecko_id']}/history. "
        f"price={computed['observed_price']} USD, "
        f"btc={computed['btc_price_observed']} USD, "
        f"return={computed['return_pct']:+.2f}%, alpha={alpha_str} "
        f"(alpha = simple difference return_pct - btc_return_pct, not a ratio)."
    )


async def update_checkpoint(
    record_id: str,
    horizon_days: int,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Record the horizon-N checkpoint for ``record_id``.

    Contract: ``docs/CONTRACTS.md`` §3.2.

    - The price is fetched **as of the target date**, never spot. The target
      date defaults to ``entry_captured_at + horizon_days``, derived from the
      record itself and not from ``now()``.
    - The BTC benchmark is fetched at that same date.
    - ``checked_{N}d_at`` is written with the true observation date.
    - A target date in the future is refused and nothing is written.
    - A checkpoint observed on any day earlier than today is a *reconstruction*
      and is stamped as such in ``outcome_notes``. If that column does not exist
      yet the write is refused, rather than silently producing a reconstructed
      checkpoint that is indistinguishable from a timely one.

    ``alpha_vs_btc_{N}d_pct`` is the simple difference
    ``return_pct - btc_return_pct`` in percentage points — a difference, not a
    ratio. See :func:`compute_returns`.
    """
    computed = await compute_checkpoint(record_id, horizon_days, as_of)
    if "error" in computed:
        return computed

    # horizon_days was validated inside compute_checkpoint; the column names are
    # looked up from the module-level literal map so that nothing
    # caller-supplied is ever interpolated into SQL.
    columns = HORIZON_COLUMNS[horizon_days]

    note = None
    if computed["is_reconstruction"]:
        note = reconstruction_note(computed, performed_on=datetime.now(timezone.utc).date())

    async with async_session() as session:
        if note is not None and not await column_exists(session, "outcome_notes"):
            return {
                "error": (
                    "refusing to write a reconstructed checkpoint: column "
                    "calibration_records.outcome_notes does not exist, so the "
                    "reconstruction cannot be marked as one. Ask agent/persistence "
                    "for the migration (docs/CONTRACTS.md §3.3)."
                )
            }

        params: dict[str, Any] = {
            "price": computed["observed_price"],
            "checked_at": computed["observed_at"],
            "btc_price": computed["btc_price_observed"],
            "return_pct": computed["return_pct"],
            "alpha_pct": computed["alpha_vs_btc_pct"],
            "id": uuid.UUID(record_id),
        }
        note_sql = ""
        if note is not None:
            note_sql = (
                ",\n                    outcome_notes = "
                "TRIM(BOTH E'\\n' FROM COALESCE(outcome_notes, '') || E'\\n\\n' || :note)"
            )
            params["note"] = note

        await session.execute(
            sql_text(
                f"""
                UPDATE calibration_records
                SET {columns["price"]} = :price,
                    {columns["checked_at"]} = :checked_at,
                    {columns["btc_price"]} = :btc_price,
                    {columns["return_pct"]} = :return_pct,
                    {columns["alpha_pct"]} = :alpha_pct{note_sql}
                WHERE id = :id
                """
            ),
            params,
        )
        await session.commit()

    return {
        "record_id": record_id,
        "project_name": computed["project_name"],
        "horizon_days": horizon_days,
        "target_date": computed["target_date"].isoformat(),
        "checked_at": computed["observed_at"].isoformat(),
        "is_reconstruction": computed["is_reconstruction"],
        "days_late": computed["days_late"],
        "entry_price": computed["entry_price"],
        "price": computed["observed_price"],
        "btc_price_at_entry": computed["btc_price_at_entry"],
        "btc_price": computed["btc_price_observed"],
        "return_pct": computed["return_pct"],
        "alpha_vs_btc_pct": computed["alpha_vs_btc_pct"],
        "alpha_definition": "simple difference: return_pct - btc_return_pct (percentage points)",
        "outcome_note": note,
    }


async def get_scorecard() -> dict[str, Any]:
    async with async_session() as session:
        result = await session.execute(
            sql_text(
                """
                SELECT recommendation, COUNT(*) as n,
                       AVG(return_30d_pct) as avg_30d,
                       AVG(return_90d_pct) as avg_90d,
                       AVG(return_180d_pct) as avg_180d,
                       AVG(alpha_vs_btc_30d_pct) as alpha_30d,
                       AVG(alpha_vs_btc_90d_pct) as alpha_90d,
                       AVG(alpha_vs_btc_180d_pct) as alpha_180d
                FROM calibration_records
                GROUP BY recommendation
                ORDER BY recommendation
                """
            )
        )
        by_recommendation = []
        for row in result.fetchall():
            by_recommendation.append(
                {
                    "recommendation": row[0],
                    "count": row[1],
                    "avg_return_30d": round(float(row[2]), 2) if row[2] is not None else None,
                    "avg_return_90d": round(float(row[3]), 2) if row[3] is not None else None,
                    "avg_return_180d": round(float(row[4]), 2) if row[4] is not None else None,
                    "avg_alpha_30d": round(float(row[5]), 2) if row[5] is not None else None,
                    "avg_alpha_90d": round(float(row[6]), 2) if row[6] is not None else None,
                    "avg_alpha_180d": round(float(row[7]), 2) if row[7] is not None else None,
                }
            )

    buy_90d = next((row["avg_return_90d"] for row in by_recommendation if row["recommendation"] == "BUY"), None)
    pass_90d = next((row["avg_return_90d"] for row in by_recommendation if row["recommendation"] == "PASS"), None)
    discrimination_90d = round(buy_90d - pass_90d, 2) if buy_90d is not None and pass_90d is not None else None

    return {
        "by_recommendation": by_recommendation,
        "discrimination_90d": discrimination_90d,
        "alpha_definition": "simple difference: return_pct - btc_return_pct (percentage points)",
        "interpretation": (
            "Positive discrimination means BUYs outperformed PASSes. "
            "Flat or negative discrimination means the committee is not separating winners from losers."
        ),
    }
