"""Client access requests: pending → approve → read-only firm book."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "access.db"
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
async def test_public_register_is_pending_until_desk_approves():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/auth/companies",
            json={
                "org_name": "Cliente Nuevo",
                "email": "cliente@nuevo.test",
                "password": "segura1234",
                "full_name": "Cliente",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body.get("pending") is True
        assert "token" not in body
        org_id = body["organization"]["id"]

        # Cannot login while pending
        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "cliente@nuevo.test", "password": "segura1234"},
        )
        assert login.status_code == 401
        assert "pendiente" in (login.json().get("detail") or "").lower()

        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        headers = {"Authorization": f"Bearer {desk_tok}"}

        listed = await client.get("/api/v1/auth/companies", headers=headers)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(i["id"] == org_id and i["status"] == "pending" for i in items)

        appr = await client.post(f"/api/v1/auth/companies/{org_id}/approve", headers=headers)
        assert appr.status_code == 200
        assert appr.json()["status"] == "approved"

        login2 = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "cliente@nuevo.test", "password": "segura1234"},
        )
        assert login2.status_code == 200, login2.text
        tok = login2.json()["token"]
        assert login2.json()["user"]["role"] == "viewer"

        # Client is read-only: cannot mutate watchlist / broker
        add = await client.post(
            "/api/v1/watchlist",
            headers={"Authorization": f"Bearer {tok}"},
            json={"ticker": "AAPL"},
        )
        assert add.status_code == 403

        broker = await client.get(
            "/api/v1/broker/status",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert broker.status_code == 403

        # Client dashboard shows firm book (monarch) — never seeds $1000 fake capital
        dash = await client.get(
            "/api/v1/dashboard",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert dash.status_code == 200
        pf = dash.json().get("portfolio")
        if pf:
            assert float(pf.get("cash") or 0) != 1000.0 or pf.get("org_id") == "monarch"


@pytest.mark.asyncio
async def test_reject_marks_status_rejected_not_pending():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/auth/companies",
            json={
                "org_name": "No gracias",
                "email": "no@gracias.test",
                "password": "segura1234",
                "full_name": "No",
            },
        )
        org_id = created.json()["organization"]["id"]
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        headers = {"Authorization": f"Bearer {desk.json()['token']}"}

        rej = await client.post(
            f"/api/v1/auth/companies/{org_id}/reject",
            headers=headers,
            json={},
        )
        assert rej.status_code == 200, rej.text
        assert rej.json()["status"] == "rejected"

        listed = await client.get("/api/v1/auth/companies", headers=headers)
        item = next(i for i in listed.json()["items"] if i["id"] == org_id)
        assert item["status"] == "rejected"

        # Still cannot login
        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "no@gracias.test", "password": "segura1234"},
        )
        assert login.status_code == 401


@pytest.mark.asyncio
async def test_desk_created_client_is_approved_viewer():
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
                "org_name": "Invitado",
                "email": "invitado@test.com",
                "password": "segura1234",
                "full_name": "Inv",
            },
        )
        assert created.status_code == 200
        assert created.json().get("pending") is False

        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "invitado@test.com", "password": "segura1234"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "viewer"


@pytest.mark.asyncio
async def test_client_deposit_request_notifies_flow():
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
                "org_name": "Depositor",
                "email": "dep@test.com",
                "password": "segura1234",
                "full_name": "Dep",
            },
        )
        org_id = created.json()["organization"]["id"]
        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "dep@test.com", "password": "segura1234"},
        )
        tok = login.json()["token"]

        req = await client.post(
            "/api/v1/auth/deposit-request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"amount_usd": 500, "note": "wire lunes"},
        )
        assert req.status_code == 200, req.text
        assert req.json()["deposit_status"] == "requested"

        mark = await client.post(
            f"/api/v1/auth/companies/{org_id}/deposit-received",
            headers={"Authorization": f"Bearer {desk_tok}"},
            json={"amount_usd": 500},
        )
        assert mark.status_code == 200
        assert mark.json()["deposit_status"] == "received"
