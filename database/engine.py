"""Database engine and session management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings
from database.models import Base
from database.url import is_sqlite, normalize_database_url, redact_database_url
from utils.logging import get_logger

logger = get_logger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

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


async def init_db() -> None:
    global _engine, _session_factory
    settings = get_settings()
    try:
        url = normalize_database_url(settings.database_url)
    except Exception as exc:
        raise RuntimeError(
            f"DATABASE_URL inválida: {exc}. "
            "En FastAPI Cloud el valor debe ser solo la URL de Neon, "
            "sin comillas. Ejemplo: postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require"
        ) from exc

    _ensure_data_dir(url)
    logger.info(
        "db.init",
        dialect="sqlite" if is_sqlite(url) else "postgresql",
        persistent=not is_sqlite(url),
        url=redact_database_url(url),
    )
    try:
        _engine = create_async_engine(url, **_engine_kwargs(url))
    except Exception as exc:
        raise RuntimeError(
            "No se pudo crear el engine de DB. Revisa DATABASE_URL en FastAPI Cloud: "
            "quita comillas (\") alrededor de la URL y no incluyas el texto DATABASE_URL=. "
            f"URL vista (redactada): {redact_database_url(url)}. Error: {exc}"
        ) from exc
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_schema(conn, url)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        await init_db()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session
