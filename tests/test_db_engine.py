"""Tests for database engine startup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
