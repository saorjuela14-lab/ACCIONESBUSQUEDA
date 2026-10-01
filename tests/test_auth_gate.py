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
async def test_root_and_dashboard_redirect_to_login_without_session():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        root = await client.get("/")
        assert root.status_code in (302, 307)
        assert "/login" in root.headers.get("location", "")

        r = await client.get("/dashboard")
        assert r.status_code in (302, 307)
        assert "/login" in r.headers.get("location", "")

        login = await client.get("/login")
        assert login.status_code == 200
        assert "Inicia sesión" in login.text or "Acceso" in login.text


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
async def test_client_monitors_firm_book_without_fake_seed():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        desk_h = {"Authorization": f"Bearer {desk_tok}"}

        default = await client.post("/api/v1/portfolios/default", headers=desk_h)
        assert default.status_code == 200
        assert default.json()["org_id"] == "monarch"

        created = await client.post(
            "/api/v1/auth/companies",
            headers=desk_h,
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
        client_h = {"Authorization": f"Bearer {tok}"}

        books = await client.get("/api/v1/portfolios", headers=client_h)
        assert books.status_code == 200
        assert any(p.get("org_id") == "monarch" for p in books.json())

        denied = await client.post("/api/v1/portfolios/default", headers=client_h)
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_cookie_only_session_opens_dashboard_without_bearer():
    """httponly cookie must be enough: no Authorization, no localStorage."""
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        login = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        assert login.status_code == 200
        set_cookie = (login.headers.get("set-cookie") or "").lower()
        assert "nexbuy_token" in set_cookie

        dash = await client.get("/dashboard")
        assert dash.status_code == 200
        assert "Monarch Capital" in dash.text
        head = dash.text.split("</head>", 1)[0]
        assert "location.replace(\"/login\")" not in head
        assert 'localStorage.getItem("nexbuy_token")' not in head

        bounced = await client.get("/login")
        assert bounced.status_code in (302, 307)
        assert "/dashboard" in bounced.headers.get("location", "")

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json().get("role") == "desk"


@pytest.mark.asyncio
async def test_login_cookie_is_encoded_and_secure_on_https():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"token": "desk-secret"},
            headers={"x-forwarded-proto": "https"},
        )
        assert login.status_code == 200
        sc = login.headers.get("set-cookie") or ""
        assert "nexbuy_token=" in sc.lower()
        assert "m1." in sc
        assert "secure" in sc.lower()
        assert "samesite=lax" in sc.lower()
        pair = sc.split(";", 1)[0]
        assert "@" not in pair
        assert "desk-secret" not in pair


@pytest.mark.asyncio
async def test_legacy_raw_cookie_still_opens_dashboard():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        dash = await client.get("/dashboard", cookies={"nexbuy_token": "desk-secret"})
        assert dash.status_code == 200
        me = await client.get("/api/v1/auth/me", cookies={"nexbuy_token": "desk-secret"})
        assert me.status_code == 200
        assert me.json().get("role") == "desk"


@pytest.mark.asyncio
async def test_mint_session_cookie_from_bearer():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        minted = await client.post(
            "/api/v1/auth/session/cookie",
            headers={"Authorization": "Bearer desk-secret", "x-forwarded-proto": "https"},
        )
        assert minted.status_code == 200
        sc = minted.headers.get("set-cookie") or ""
        assert "m1." in sc
        assert "secure" in sc.lower()


@pytest.mark.asyncio
async def test_sw_kill_switch_is_public():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        for path in ("/sw.js", "/dashboard/sw.js"):
            r = await client.get(path)
            assert r.status_code == 200, path
            assert "unregister" in r.text
            assert r.headers.get("service-worker-allowed") == "/"
            assert "no-store" in (r.headers.get("cache-control") or "")


@pytest.mark.asyncio
async def test_login_html_requires_cookie_before_dashboard_redirect():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        page = await client.get("/login")
        assert page.status_code == 200
        assert "enterDashboardIfCookie" in page.text
        assert "/auth/session/cookie" in page.text
        assert "monarch_login_hops" in page.text
