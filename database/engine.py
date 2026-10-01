"""Database engine and session management."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings
from database.models import Base
from database.url import (
    database_host,
    is_sqlite,
    normalize_database_url,
    sanitize_db_error,
)
from utils.logging import get_logger

logger = get_logger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_db_ready = False
_db_last_error: str | None = None
_reconnect_task: asyncio.Task | None = None

# 6 attempts, ~52s of sleeps + fast-fail DNS. Lifespan then continues degraded.
STARTUP_ATTEMPT_DELAYS: tuple[float, ...] = (0, 2, 4, 8, 16, 20)
BACKGROUND_RETRY_INTERVAL = 30.0

_ORG_TABLES = ("portfolios", "watchlist", "alerts")


def _ensure_data_dir(url: str) -> None:
    if is_sqlite(url) and ":///" in url:
        db_path = url.split("///", 1)[1]
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


async def _table_columns_sqlite(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _table_columns_pg(conn, table: str) -> set[str]:
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"
        ),
        {"t": table},
    )
    return {row[0] for row in result.fetchall()}


async def _migrate_schema(conn, url: str) -> None:
    """Lightweight migrations (add columns if missing)."""
    sqlite = is_sqlite(url)

    async def cols(table: str) -> set[str]:
        if sqlite:
            return await _table_columns_sqlite(conn, table)
        return await _table_columns_pg(conn, table)

    # portfolios.mode (legacy)
    pcols = await cols("portfolios")
    if pcols and "mode" not in pcols:
        await conn.execute(
            text("ALTER TABLE portfolios ADD COLUMN mode VARCHAR(16) DEFAULT 'real'")
        )

    # org_id on tenant tables
    for table in _ORG_TABLES:
        tcols = await cols(table)
        if not tcols:
            continue
        if "org_id" not in tcols:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN org_id VARCHAR(36)")
            )
            logger.info("db.migrate.add_org_id", table=table)

    # Backfill NULL org rows to monarch so desk still sees legacy data;
    # company tenants only see their own org_id.
    for table in _ORG_TABLES:
        tcols = await cols(table)
        if "org_id" in tcols:
            await conn.execute(
                text(f"UPDATE {table} SET org_id = 'monarch' WHERE org_id IS NULL")
            )

    # WhatsApp notify fields on users / organizations
    for table, columns in (
        ("users", ("notify_phone", "notify_whatsapp_key")),
        ("organizations", ("notify_phone", "notify_whatsapp_key")),
    ):
        tcols = await cols(table)
        if not tcols:
            continue
        for col in columns:
            if col not in tcols:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR(128)"))
                logger.info("db.migrate.add_column", table=table, column=col)

    # Client deposit / access fields on organizations
    ocols = await cols("organizations")
    if ocols:
        if "deposit_status" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN deposit_status VARCHAR(24) DEFAULT 'none'")
            )
            logger.info("db.migrate.add_column", table="organizations", column="deposit_status")
        if "deposit_requested_usd" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN deposit_requested_usd FLOAT")
            )
            logger.info("db.migrate.add_column", table="organizations", column="deposit_requested_usd")
        if "deposit_note" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN deposit_note VARCHAR(280)")
            )
            logger.info("db.migrate.add_column", table="organizations", column="deposit_note")
        if "withdrawal_status" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN withdrawal_status VARCHAR(24) DEFAULT 'none'")
            )
            logger.info("db.migrate.add_column", table="organizations", column="withdrawal_status")
        if "withdrawal_requested_usd" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN withdrawal_requested_usd FLOAT")
            )
            logger.info("db.migrate.add_column", table="organizations", column="withdrawal_requested_usd")
        if "withdrawal_note" not in ocols:
            await conn.execute(
                text("ALTER TABLE organizations ADD COLUMN withdrawal_note VARCHAR(280)")
            )
            logger.info("db.migrate.add_column", table="organizations", column="withdrawal_note")

    # Daily learning: error_tag on investment_memory
    imcols = await cols("investment_memory")
    if imcols and "error_tag" not in imcols:
        await conn.execute(
            text("ALTER TABLE investment_memory ADD COLUMN error_tag VARCHAR(32)")
        )
        logger.info("db.migrate.add_column", table="investment_memory", column="error_tag")
    if imcols and "briefs_json" not in imcols:
        await conn.execute(
            text("ALTER TABLE investment_memory ADD COLUMN briefs_json TEXT DEFAULT '{}'")
        )
        logger.info("db.migrate.add_column", table="investment_memory", column="briefs_json")


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"echo": False}
    if not is_sqlite(url):
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_size": 5,
                "max_overflow": 5,
                "pool_recycle": 300,
            }
        )
    return kwargs


def db_snapshot() -> dict:
    """Process-safe DB status for /health. Host only — never credentials."""
    settings = get_settings()
    url = normalize_database_url(settings.database_url)
    return {
        "ready": _db_ready,
        "host": database_host(url),
        "error": _db_last_error,
        "dialect": "sqlite" if is_sqlite(url) else "postgresql",
    }


def reset_db_runtime() -> None:
    """Test helper — drop in-process engine state."""
    global _engine, _session_factory, _db_ready, _db_last_error, _reconnect_task
    _engine = None
    _session_factory = None
    _db_ready = False
    _db_last_error = None
    _reconnect_task = None


async def _dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        try:
            await _engine.dispose()
        except Exception:
            pass
    _engine = None
    _session_factory = None


async def _open_schema(url: str) -> None:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(url, **_engine_kwargs(url))
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_schema(conn, url)


async def init_db(*, attempt_delays: tuple[float, ...] | None = None) -> bool:
    """Create engine and migrate. Retries DNS/connect; never raises on failure.

    Returns True when the schema is reachable. False → degraded (process stays up).
    """
    global _db_ready, _db_last_error
    if _db_ready and _session_factory is not None:
        return True
    settings = get_settings()
    url = normalize_database_url(settings.database_url)
    host = database_host(url)
    _ensure_data_dir(url)
    logger.info(
        "db.init",
        dialect="sqlite" if is_sqlite(url) else "postgresql",
        persistent=not is_sqlite(url),
        host=host,
    )
    delays = STARTUP_ATTEMPT_DELAYS if attempt_delays is None else attempt_delays
    for i, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await _open_schema(url)
            _db_ready = True
            _db_last_error = None
            logger.info("db.ready", host=host, attempt=i)
            return True
        except Exception as exc:
            _db_ready = False
            _db_last_error = sanitize_db_error(exc)
            logger.warning(
                "db.connect_failed",
                host=host,
                attempt=i,
                attempts=len(delays),
                error=_db_last_error,
            )
            await _dispose_engine()
    logger.error("db.degraded", host=host, error=_db_last_error)
    return False


async def reconnect_until_ready() -> None:
    """Background retries after degraded startup (Neon wake / transient DNS)."""
    while not _db_ready:
        await asyncio.sleep(BACKGROUND_RETRY_INTERVAL)
        try:
            if await init_db(attempt_delays=(0,)):
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("db.reconnect_loop", error=sanitize_db_error(exc))


def spawn_reconnect() -> asyncio.Task | None:
    global _reconnect_task
    if _db_ready:
        return None
    if _reconnect_task is not None and not _reconnect_task.done():
        return _reconnect_task
    try:
        _reconnect_task = asyncio.create_task(reconnect_until_ready())
    except RuntimeError:
        _reconnect_task = None
    return _reconnect_task


async def shutdown_db() -> None:
    global _reconnect_task
    if _reconnect_task is not None and not _reconnect_task.done():
        _reconnect_task.cancel()
        try:
            await _reconnect_task
        except (asyncio.CancelledError, Exception):
            pass
    _reconnect_task = None
    await _dispose_engine()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if not _db_ready or _session_factory is None:
        await init_db(attempt_delays=(0,))
    if _session_factory is None or not _db_ready:
        raise RuntimeError(_db_last_error or "database unavailable")
    async with _session_factory() as session:
        yield session
