"""Liveness must stay 200 even when Neon/DNS is down."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.url import NEON_DIRECT_HOST  # used in the mocked snapshot host


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/health.db")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_200_reports_db_down():
    app = create_app()
    transport = ASGITransport(app=app)
    with patch(
        "apis.routes.health.db_snapshot",
        return_value={
            "ready": False,
            "host": NEON_DIRECT_HOST,
            "error": "OSError: Temporary failure in name resolution",
            "dialect": "postgresql",
        },
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["db"] == "down"
    assert "db_host" not in body
    assert "name resolution" in body["db_error"]
    assert "password" not in body["db_error"].lower()


@pytest.mark.asyncio
async def test_lifespan_does_not_crash_when_db_init_fails():
    from apis.app import lifespan

    with patch("apis.app.init_db", new=AsyncMock(return_value=False)), \
         patch("apis.app.spawn_reconnect", return_value=None), \
         patch("apis.app.shutdown_db", new=AsyncMock()):
        app = create_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/health")
                assert r.status_code == 200
                assert r.json()["status"] == "healthy"
