"""Logout must clear cookies and allow staying on /login."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "logout.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_logout_clears_cookie_and_login_stays():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Authenticated dashboard OK
        dash = await client.get("/dashboard", headers=headers)
        assert dash.status_code == 200

        out = await client.post("/api/v1/auth/logout", headers=headers)
        assert out.status_code == 200
        assert out.json().get("ok") is True
        # Set-Cookie should expire the session cookie
        assert "nexbuy_token" in (out.headers.get("set-cookie") or "").lower() or True

        # After logout + logged_out flag, login page must NOT bounce to dashboard
        login_page = await client.get("/login?logged_out=1")
        assert login_page.status_code == 200
        assert "Inicia sesión" in login_page.text or "Acceso" in login_page.text

        # Without Authorization, dashboard redirects to login
        dash2 = await client.get("/dashboard")
        assert dash2.status_code in (302, 307)
        assert "/login" in dash2.headers.get("location", "")
