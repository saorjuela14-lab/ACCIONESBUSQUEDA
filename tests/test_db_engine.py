"""Tests for database URL normalization and engine init."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.url import (
    NEON_DIRECT_HOST,
    NEON_POOLER_HOST,
    database_host,
    is_postgres,
    is_sqlite,
    normalize_database_url,
    sanitize_db_error,
)


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


def test_neon_curly_surf_hosts_survive_normalize():
    """DATABASE_URL host must stay the Neon endpoint — no rewrite of -pooler / c-12."""
    for host in (NEON_DIRECT_HOST, NEON_POOLER_HOST):
        raw = (
            f"postgresql://neondb_owner:p%40ssword@{host}/neondb"
            "?sslmode=require&channel_binding=require"
        )
        out = normalize_database_url(raw)
        assert out.startswith("postgresql+asyncpg://")
        assert database_host(out) == host
        assert host in out
        assert "sslmode=" not in out
        assert "channel_binding" not in out
        assert "ssl=require" in out
        assert "p%40ssword" in out
        assert "neondb_owner:p@ssword" not in out


def test_database_host_never_includes_password():
    url = f"postgresql://u:super-secret@{NEON_DIRECT_HOST}/neondb"
    host = database_host(url)
    assert host == NEON_DIRECT_HOST
    assert host and "secret" not in host


def test_sanitize_db_error_strips_credentials():
    err = OSError(
        f'could not connect to postgresql://u:s3cret@{NEON_DIRECT_HOST}/neondb'
    )
    text = sanitize_db_error(err)
    assert "s3cret" not in text
    assert "***" in text or "OSError" in text


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

        engine.reset_db_runtime()
        ok = await engine.init_db()

    assert ok is True
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

        engine.reset_db_runtime()
        await engine.init_db()

    args, kwargs = ce.call_args
    assert args[0].startswith("postgresql+asyncpg://")
    assert kwargs.get("pool_pre_ping") is True


@pytest.mark.asyncio
async def test_init_db_dns_failure_is_degraded_not_fatal():
    mock_settings = MagicMock()
    mock_settings.database_url = (
        f"postgresql://u:p@{NEON_DIRECT_HOST}/neondb?sslmode=require"
    )
    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(
        side_effect=OSError("[Errno -3] Temporary failure in name resolution")
    )
    mock_engine.dispose = AsyncMock()

    with patch("database.engine.get_settings", return_value=mock_settings), \
         patch("database.engine.create_async_engine", return_value=mock_engine), \
         patch("database.engine.async_sessionmaker"), \
         patch("database.engine.STARTUP_ATTEMPT_DELAYS", (0, 0)):
        import database.engine as engine

        engine.reset_db_runtime()
        ok = await engine.init_db()
        snap = engine.db_snapshot()

    assert ok is False
    assert snap["ready"] is False
    assert snap["host"] == NEON_DIRECT_HOST
    assert "name resolution" in (snap["error"] or "")
    assert "p@" not in (snap["error"] or "")
