"""Login gate: unauthenticated terminal redirects; company gets a portfolio."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "gate.db"
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
async def test_dashboard_redirects_to_login_without_session():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        r = await client.get("/dashboard")
        assert r.status_code in (302, 307)
        assert "/login" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_login_with_desk_opens_dashboard():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        token = login.json()["token"]
        dash = await client.get(
            "/dashboard", headers={"Authorization": f"Bearer {token}"}
        )
        # FileResponse 200 when authenticated
        assert dash.status_code == 200


@pytest.mark.asyncio
async def test_company_gets_portfolio_on_create_and_default():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        created = await client.post(
            "/api/v1/auth/companies",
            headers={"Authorization": f"Bearer {desk_tok}"},
            json={
                "org_name": "Acme",
                "email": "ops@acme.test",
                "password": "segura1234",
                "full_name": "Ops",
            },
        )
        assert created.status_code == 200

        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "ops@acme.test", "password": "segura1234"},
        )
        tok = login.json()["token"]
        books = await client.get(
            "/api/v1/portfolios", headers={"Authorization": f"Bearer {tok}"}
        )
        assert books.status_code == 200
        assert len(books.json()) >= 1

        default = await client.post(
            "/api/v1/portfolios/default",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert default.status_code == 200
        assert default.json()["org_id"]
        # Idempotent — does not create a second book
        books2 = await client.get(
            "/api/v1/portfolios", headers={"Authorization": f"Bearer {tok}"}
        )
        assert len(books2.json()) == len(books.json())
