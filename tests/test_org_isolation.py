"""Org-scoped data isolation for B2B tenants."""

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
async def test_company_cannot_see_other_org_watchlist():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]

        # Create two companies via desk
        for email, name in (("a@acme.test", "Acme"), ("b@beta.test", "Beta")):
            r = await client.post(
                "/api/v1/auth/companies",
                headers={"Authorization": f"Bearer {desk_tok}"},
                json={
                    "org_name": name,
                    "email": email,
                    "password": "segura1234",
                    "full_name": name,
                },
            )
            assert r.status_code == 200, r.text

        login_a = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "a@acme.test", "password": "segura1234"},
        )
        tok_a = login_a.json()["token"]
        login_b = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "b@beta.test", "password": "segura1234"},
        )
        tok_b = login_b.json()["token"]

        # A adds NVDA
        add = await client.post(
            "/api/v1/watchlist",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"ticker": "NVDA"},
        )
        assert add.status_code == 200, add.text

        list_a = await client.get(
            "/api/v1/watchlist", headers={"Authorization": f"Bearer {tok_a}"}
        )
        list_b = await client.get(
            "/api/v1/watchlist", headers={"Authorization": f"Bearer {tok_b}"}
        )
        assert any(i["ticker"] == "NVDA" for i in list_a.json())
        assert list_b.json() == []

        # Portfolios isolated
        pa = await client.post(
            "/api/v1/portfolios",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={
                "name": "Acme Book",
                "strategy": "growth_investing",
                "initial_capital": 5000,
                "mode": "demo",
            },
        )
        assert pa.status_code == 200, pa.text
        books_b = await client.get(
            "/api/v1/portfolios", headers={"Authorization": f"Bearer {tok_b}"}
        )
        assert all(p["name"] != "Acme Book" for p in books_b.json())

        # B cannot sync Alpaca (desk-only)
        sync_b = await client.post(
            "/api/v1/portfolios/sync-alpaca",
            headers={"Authorization": f"Bearer {tok_b}"},
        )
        assert sync_b.status_code == 403

        # Desk can see Acme watchlist in unfiltered admin list
        desk_wl = await client.get(
            "/api/v1/watchlist", headers={"Authorization": f"Bearer {desk_tok}"}
        )
        assert desk_wl.status_code == 200
        assert any(i["ticker"] == "NVDA" for i in desk_wl.json())
