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


def _ensure_data_dir(url: str) -> None:
    if is_sqlite(url) and ":///" in url:
        db_path = url.split("///", 1)[1]
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


async def _migrate_schema(conn, url: str) -> None:
    """Lightweight migrations (add columns if missing)."""
    if is_sqlite(url):
        result = await conn.execute(text("PRAGMA table_info(portfolios)"))
        cols = {row[1] for row in result.fetchall()}
        if "mode" not in cols:
            await conn.execute(
                text("ALTER TABLE portfolios ADD COLUMN mode VARCHAR(16) DEFAULT 'real'")
            )
        return

    # Postgres: create_all handles new installs; add column if upgrading old schema
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'portfolios'"
        )
    )
    cols = {row[0] for row in result.fetchall()}
    if cols and "mode" not in cols:
        await conn.execute(
            text("ALTER TABLE portfolios ADD COLUMN mode VARCHAR(16) DEFAULT 'real'")
        )


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"echo": False}
    if not is_sqlite(url):
        # Neon / serverless-friendly
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
