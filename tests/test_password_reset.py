"""Password recovery for approved company clients."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "pwd.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _approved_client(client: AsyncClient) -> tuple[str, dict]:
    desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
    desk_tok = desk.json()["token"]
    headers = {"Authorization": f"Bearer {desk_tok}"}
    created = await client.post(
        "/api/v1/auth/companies",
        headers=headers,
        json={
            "org_name": "Reset Co",
            "email": "reset@co.test",
            "password": "antigua123",
            "full_name": "Reset",
        },
    )
    assert created.status_code == 200, created.text
    return "reset@co.test", headers


@pytest.mark.asyncio
async def test_forgot_and_reset_password_flow():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email, desk_h = await _approved_client(client)

        forgot = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": email},
        )
        assert forgot.status_code == 200
        body = forgot.json()
        assert body.get("ok") is True
        assert "code" not in body  # never leak code publicly
        assert body.get("request_id")
        assert body.get("email") == email
        request_id = body["request_id"]

        listed = await client.get("/api/v1/auth/password/resets", headers=desk_h)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        code = items[0]["code"]
        assert code and len(code) == 6

        # Wrong email + same request_id must fail
        wrong_email = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": "otro@co.test",
                "code": code,
                "new_password": "nueva1234",
                "request_id": request_id,
            },
        )
        assert wrong_email.status_code == 400

        bad = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": email,
                "code": "000000",
                "new_password": "nueva1234",
                "request_id": request_id,
            },
        )
        assert bad.status_code == 400

        ok = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": email,
                "code": code,
                "new_password": "nueva1234",
                "request_id": request_id,
            },
        )
        assert ok.status_code == 200, ok.text

        # Old password fails
        old = await client.post(
            "/api/v1/auth/company/login",
            json={"email": email, "password": "antigua123"},
        )
        assert old.status_code == 401

        # New password works
        neo = await client.post(
            "/api/v1/auth/company/login",
            json={"email": email, "password": "nueva1234"},
        )
        assert neo.status_code == 200


@pytest.mark.asyncio
async def test_forgot_unknown_email_is_generic():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "nobody@nowhere.test"},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True


@pytest.mark.asyncio
async def test_desk_can_set_password_directly():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email, desk_h = await _approved_client(client)
        setp = await client.post(
            "/api/v1/auth/password/desk-set",
            headers=desk_h,
            json={"email": email, "new_password": "mesaSet123"},
        )
        assert setp.status_code == 200, setp.text
        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": email, "password": "mesaSet123"},
        )
        assert login.status_code == 200
