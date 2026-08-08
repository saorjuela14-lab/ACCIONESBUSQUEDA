"""Clients monitor the shared firm book; only the mesa mutates capital."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "org.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_client_is_read_only_and_sees_firm_book():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        desk_h = {"Authorization": f"Bearer {desk_tok}"}

        # Desk seeds the firm watchlist + book
        add = await client.post(
            "/api/v1/watchlist",
            headers=desk_h,
            json={"ticker": "NVDA"},
        )
        assert add.status_code == 200, add.text

        created = await client.post(
            "/api/v1/auth/companies",
            headers=desk_h,
            json={
                "org_name": "Acme",
                "email": "a@acme.test",
                "password": "segura1234",
                "full_name": "Acme",
            },
        )
        assert created.status_code == 200, created.text

        login_a = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "a@acme.test", "password": "segura1234"},
        )
        tok_a = login_a.json()["token"]
        client_h = {"Authorization": f"Bearer {tok_a}"}

        # Client can READ firm watchlist (monarch)
        list_a = await client.get("/api/v1/watchlist", headers=client_h)
        assert list_a.status_code == 200
        assert any(i["ticker"] == "NVDA" for i in list_a.json())

        # Client cannot WRITE
        denied = await client.post(
            "/api/v1/watchlist",
            headers=client_h,
            json={"ticker": "TSLA"},
        )
        assert denied.status_code == 403

        sync_b = await client.post("/api/v1/portfolios/sync-alpaca", headers=client_h)
        assert sync_b.status_code == 403

        # Desk still sees NVDA
        desk_wl = await client.get("/api/v1/watchlist", headers=desk_h)
        assert desk_wl.status_code == 200
        assert any(i["ticker"] == "NVDA" for i in desk_wl.json())
