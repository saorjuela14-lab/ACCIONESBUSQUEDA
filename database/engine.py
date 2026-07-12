"""Database engine and session management."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from database.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_data_dir(url: str) -> None:
    if "sqlite" in url and ":///" in url:
        db_path = url.split("///", 1)[1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


async def _migrate_schema(conn) -> None:
    """Lightweight migrations for SQLite (add columns if missing)."""
    result = await conn.execute(text("PRAGMA table_info(portfolios)"))
    cols = {row[1] for row in result.fetchall()}
    if "mode" not in cols:
        await conn.execute(text("ALTER TABLE portfolios ADD COLUMN mode VARCHAR(16) DEFAULT 'real'"))


async def init_db() -> None:
    global _engine, _session_factory
    settings = get_settings()
    _ensure_data_dir(settings.database_url)
    _engine = create_async_engine(settings.database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_schema(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        await init_db()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session
