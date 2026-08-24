from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
    pool_size=20,
    max_overflow=10,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Forward-only SQL migration runner
# ---------------------------------------------------------------------------
#
# `backend/init.sql` runs ONLY when Postgres initialises an empty data
# directory. The live volume has weeks of uptime, so it will never see a new
# statement added to that file. Every schema change therefore also ships as a
# numbered, idempotent SQL file in `backend/migrations/`.
#
# Design rules (see backend/migrations/README.md):
#   * forward-only — there are no downgrades
#   * every file must be idempotent (IF NOT EXISTS / guarded DO blocks) so that
#     applying it to a volume that already got the change from init.sql is a
#     no-op. This is what keeps init.sql and the migrations from drifting: the
#     migrations are authoritative and always run; init.sql is only a
#     fresh-volume fast path.
#   * 0001 is a no-op baseline that stamps the schema as it existed on
#     24 Aug 2026. It creates nothing.

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_MIGRATION_FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

# Arbitrary but fixed key so that two backends starting at once serialise.
_ADVISORY_LOCK_KEY = 81002026

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _asyncpg_dsn(url: str) -> str:
    """Strip the SQLAlchemy dialect prefix so asyncpg can consume the URL."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def discover_migrations(migrations_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """Return [(version, name, path)] sorted by version."""
    directory = migrations_dir or MIGRATIONS_DIR
    if not directory.is_dir():
        return []

    found: list[tuple[str, str, Path]] = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        match = _MIGRATION_FILENAME.match(path.name)
        if not match:
            logger.warning("Ignoring migration file with non-conforming name: %s", path.name)
            continue
        found.append((match.group(1), match.group(2), path))

    versions = [version for version, _, _ in found]
    duplicates = {version for version in versions if versions.count(version) > 1}
    if duplicates:
        raise RuntimeError(f"Duplicate migration version(s): {sorted(duplicates)}")

    return sorted(found, key=lambda item: item[0])


async def run_migrations(migrations_dir: Path | None = None) -> dict[str, Any]:
    """Apply every unapplied migration in order. Idempotent.

    Never raises. Returns a summary dict:
        {"applied": [...], "skipped": [...], "errors": [...], "ok": bool}

    Called at startup it must not take the service down, so failures are
    logged at ERROR and reported in the return value rather than raised. The
    CLI entry point (``python -m app.database``) turns a non-ok result into a
    non-zero exit status.
    """
    summary: dict[str, Any] = {"applied": [], "skipped": [], "errors": [], "ok": True}

    try:
        migrations = discover_migrations(migrations_dir)
    except Exception as exc:
        logger.error("MIGRATIONS: discovery failed: %s", exc)
        summary["errors"].append(f"discovery: {exc}")
        summary["ok"] = False
        return summary

    if not migrations:
        logger.warning(
            "MIGRATIONS: no migration files found under %s",
            migrations_dir or MIGRATIONS_DIR,
        )
        return summary

    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - asyncpg is a hard dependency
        logger.error("MIGRATIONS: asyncpg unavailable: %s", exc)
        summary["errors"].append(f"asyncpg: {exc}")
        summary["ok"] = False
        return summary

    try:
        conn = await asyncpg.connect(_asyncpg_dsn(settings.database_url))
    except Exception as exc:
        logger.error("MIGRATIONS: could not connect to the database: %s", exc)
        summary["errors"].append(f"connect: {exc}")
        summary["ok"] = False
        return summary

    try:
        await conn.execute(_LEDGER_DDL)
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
            already = {row["version"]: row["checksum"] for row in rows}

            for version, name, path in migrations:
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

                if version in already:
                    if already[version] != checksum:
                        message = (
                            f"{version}_{name}.sql has changed since it was applied "
                            f"(recorded {already[version][:12]}, on disk {checksum[:12]}). "
                            "Applied migrations are immutable — ship a new migration instead."
                        )
                        logger.error("MIGRATIONS: %s", message)
                        summary["errors"].append(message)
                        summary["ok"] = False
                    summary["skipped"].append(version)
                    continue

                try:
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations (version, name, checksum) "
                            "VALUES ($1, $2, $3)",
                            version,
                            name,
                            checksum,
                        )
                except Exception as exc:
                    message = f"{version}_{name}.sql failed: {exc}"
                    logger.error("MIGRATIONS: %s", message)
                    summary["errors"].append(message)
                    summary["ok"] = False
                    # Ordered and forward-only: a later migration may depend on
                    # this one, so stop rather than apply out of order.
                    break

                logger.info("MIGRATIONS: applied %s_%s", version, name)
                summary["applied"].append(version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)
    except Exception as exc:
        logger.error("MIGRATIONS: unexpected failure: %s", exc)
        summary["errors"].append(str(exc))
        summary["ok"] = False
    finally:
        await conn.close()

    logger.info(
        "MIGRATIONS: %d applied, %d already present, %d error(s)",
        len(summary["applied"]),
        len(summary["skipped"]),
        len(summary["errors"]),
    )
    return summary


def _main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    result = asyncio.run(run_migrations())
    print(f"applied={result['applied']} skipped={result['skipped']} errors={result['errors']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
