"""Deposit into firm Alpaca book + withdrawal approval flow."""

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "capital.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("ALPACA_FUNDING_URL", "https://app.alpaca.markets/")  # must be blocked
    monkeypatch.setenv("ALPACA_FUNDING_BANK_NAME", "Test Bank")
    monkeypatch.setenv("ALPACA_FUNDING_ROUTING_NUMBER", "021000021")
    monkeypatch.setenv("ALPACA_FUNDING_ACCOUNT_NUMBER", "123456789")
    monkeypatch.setenv("ALPACA_FUNDING_BENEFICIARY", "Monarch Capital")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _client_session(client: AsyncClient):
    desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
    desk_h = {"Authorization": f"Bearer {desk.json()['token']}"}
    created = await client.post(
        "/api/v1/auth/companies",
        headers=desk_h,
        json={
            "org_name": "Fund Co",
            "email": "fund@co.test",
            "password": "segura1234",
            "full_name": "Fund",
        },
    )
    assert created.status_code == 200
    login = await client.post(
        "/api/v1/auth/company/login",
        json={"email": "fund@co.test", "password": "segura1234"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}, desk_h


@pytest.mark.asyncio
async def test_deposit_returns_funding_and_client_confirm():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_h, desk_h = await _client_session(client)

        dep = await client.post(
            "/api/v1/auth/capital/deposit",
            headers=client_h,
            json={"amount_usd": 250, "note": "wire"},
        )
        assert dep.status_code == 200, dep.text
        body = dep.json()
        # Alpaca login URLs are blocked — clients only get bank transfer details
        assert body["funding"]["funding_url"] == ""
        assert body["funding"]["no_alpaca_login"] is True
        assert body["funding"]["bank"]["routing_number"] == "021000021"
        assert body["funding"]["bank"]["account_number"] == "123456789"
        assert body["funding"]["memo_reference"] == "fund@co.test"
        assert body["funding"]["shared_account"] is True
        assert "Alpaca" in (body["funding"].get("headline") or "")
        req_id = body["request"]["id"]
        assert body["request"]["status"] == "requested"

        conf = await client.post(
            f"/api/v1/auth/capital/deposit/{req_id}/confirm",
            headers=client_h,
            json={},
        )
        assert conf.status_code == 200
        assert conf.json()["request"]["status"] == "client_confirmed"

        recv = await client.post(
            f"/api/v1/auth/capital/{req_id}/received",
            headers=desk_h,
            json={},
        )
        assert recv.status_code == 200
        assert recv.json()["request"]["status"] == "received"


@pytest.mark.asyncio
async def test_withdrawal_requires_desk_approval():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_h, desk_h = await _client_session(client)

        w = await client.post(
            "/api/v1/auth/capital/withdraw",
            headers=client_h,
            json={"amount_usd": 80, "note": "necesito liquidez"},
        )
        assert w.status_code == 200, w.text
        req_id = w.json()["request"]["id"]
        assert w.json()["request"]["status"] == "requested"

        # Client cannot approve
        denied = await client.post(
            f"/api/v1/auth/capital/{req_id}/approve",
            headers=client_h,
            json={},
        )
        assert denied.status_code == 403

        appr = await client.post(
            f"/api/v1/auth/capital/{req_id}/approve",
            headers=desk_h,
            json={},
        )
        assert appr.status_code == 200
        assert appr.json()["request"]["status"] == "approved"

        paid = await client.post(
            f"/api/v1/auth/capital/{req_id}/paid",
            headers=desk_h,
            json={},
        )
        assert paid.status_code == 200
        assert paid.json()["request"]["status"] == "paid"

        listed = await client.get("/api/v1/auth/capital/requests", headers=desk_h)
        assert listed.status_code == 200
        assert any(i["id"] == req_id for i in listed.json()["items"])
