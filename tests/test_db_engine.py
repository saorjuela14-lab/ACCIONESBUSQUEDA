"""Tests for database URL normalization and engine init."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.url import is_postgres, is_sqlite, normalize_database_url


def test_normalize_strips_quotes_and_prefix():
    quoted = normalize_database_url(
        '"postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require"'
    )
    assert quoted.startswith("postgresql+asyncpg://")
    assert "sslmode=" not in quoted
    assert "ssl=require" in quoted

    curly = normalize_database_url(
        "“postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require”"
    )
    assert curly.startswith("postgresql+asyncpg://")

    prefixed = normalize_database_url(
        "DATABASE_URL=postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require"
    )
    assert prefixed.startswith("postgresql+asyncpg://")
    assert "@ep-x.neon.tech" in prefixed


def test_normalize_postgres_urls():
    assert normalize_database_url("postgres://u:p@h/db").startswith("postgresql+asyncpg://")
    assert normalize_database_url("postgresql://u:p@h/db").startswith("postgresql+asyncpg://")
    assert (
        normalize_database_url("postgresql+asyncpg://u:p@h/db")
        == "postgresql+asyncpg://u:p@h/db"
    )
    neon = normalize_database_url(
        "postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require"
    )
    assert neon.startswith("postgresql+asyncpg://")
    assert "sslmode=" not in neon
    assert "ssl=require" in neon


def test_normalize_sqlite_unchanged():
    url = "sqlite+aiosqlite:///./data/nexbuy.db"
    assert normalize_database_url(url) == url
    assert is_sqlite(url)
    assert not is_postgres(url)


def test_normalize_empty_defaults_sqlite():
    assert "sqlite" in normalize_database_url("")


@pytest.mark.asyncio
async def test_init_db_uses_get_settings():
    """init_db must import and call get_settings (regression for NameError on deploy)."""
    mock_settings = MagicMock()
    mock_settings.database_url = "sqlite+aiosqlite:///:memory:"

    mock_conn = AsyncMock()
    mock_conn.run_sync = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_begin.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_begin)

    with patch("database.engine.get_settings", return_value=mock_settings) as gs, \
         patch("database.engine.create_async_engine", return_value=mock_engine) as ce, \
         patch("database.engine.async_sessionmaker") as sf, \
         patch("database.engine._migrate_schema", new_callable=AsyncMock) as migrate:
        import database.engine as engine

        engine._engine = None
        engine._session_factory = None
        await engine.init_db()

    gs.assert_called_once()
    ce.assert_called_once_with("sqlite+aiosqlite:///:memory:", echo=False)
    migrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_db_postgres_uses_pool_kwargs():
    mock_settings = MagicMock()
    mock_settings.database_url = "postgresql://u:p@localhost/db"

    mock_conn = AsyncMock()
    mock_conn.run_sync = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_begin.__aexit__ = AsyncMock(return_value=None)
    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_begin)

    with patch("database.engine.get_settings", return_value=mock_settings), \
         patch("database.engine.create_async_engine", return_value=mock_engine) as ce, \
         patch("database.engine.async_sessionmaker"), \
         patch("database.engine._migrate_schema", new_callable=AsyncMock):
        import database.engine as engine

        engine._engine = None
        engine._session_factory = None
        await engine.init_db()

    args, kwargs = ce.call_args
    assert args[0].startswith("postgresql+asyncpg://")
    assert kwargs.get("pool_pre_ping") is True
