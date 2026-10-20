"""Cumplimiento legal: Ley 1581 (privacidad), términos, consentimiento, habeas data."""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apis.app import create_app
from config.legal import (
    AUDIT_ACTION_CONSENT,
    AUDIT_ACTION_DATA_REQUEST,
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)
from config.settings import get_settings
from database.engine import init_db


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "legal.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    get_settings.cache_clear()
    from database.engine import reset_db_runtime

    reset_db_runtime()  # fresh engine per test (tmp sqlite)
    yield
    reset_db_runtime()
    get_settings.cache_clear()


@pytest.fixture
def pushes(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_notify(self, title, body, *a, **k):
        sent.append((title, body))
        return {"ok": True}

    from services.push_notification_service import PushNotificationService

    monkeypatch.setattr(PushNotificationService, "notify_message", fake_notify)
    return sent


async def _client():
    await init_db()
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


SIGNUP = {
    "org_name": "Titular SAS",
    "email": "titular@legal.test",
    "password": "segura1234",
    "full_name": "Ana Titular",
}


@pytest.mark.asyncio
async def test_pitch_not_served_publicly():
    async with await _client() as c:
        r = await c.get("/dashboard/static/pitch/MONARCH_CAPITAL_SHARK_TANK.md")
        assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path,needle", [("/privacidad", "Ley 1581"), ("/terminos", "pérdida total")])
async def test_legal_pages_public(path, needle):
    async with await _client() as c:
        r = await c.get(path)
        assert r.status_code == 200
        assert needle in r.text
        assert "[NIT]" in r.text
        # POST is not opened by the allowlist
        assert (await c.post(path)).status_code in (401, 405)


@pytest.mark.asyncio
async def test_allowlist_does_not_open_other_routes():
    async with await _client() as c:
        assert (await c.get("/privacidad/../api/v1/portfolios")).status_code != 200
        assert (await c.get("/privacidadx")).status_code == 401
        assert (await c.get("/api/v1/auth/data-requests")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["accept_privacy_terms", "declare_adult", "both"])
async def test_signup_rejected_without_consents(missing, pushes):
    body = {**SIGNUP, "accept_privacy_terms": True, "declare_adult": True}
    if missing == "both":
        body.pop("accept_privacy_terms"); body.pop("declare_adult")
    else:
        body[missing] = False
    async with await _client() as c:
        r = await c.post("/api/v1/auth/companies", json=body)
        assert r.status_code == 422
    assert pushes == []


@pytest.mark.asyncio
async def test_signup_stores_consent_proof(pushes):
    from database.engine import get_session
    from database.models import AuditEventORM

    async with await _client() as c:
        r = await c.post(
            "/api/v1/auth/companies",
            json={**SIGNUP, "accept_privacy_terms": True, "declare_adult": True},
            headers={"user-agent": "pytest-UA", "x-forwarded-for": "203.0.113.7"},
        )
        assert r.status_code == 200, r.text
        org_id = r.json()["organization"]["id"]
    async for s in get_session():
        rows = (await s.execute(select(AuditEventORM).where(AuditEventORM.action == AUDIT_ACTION_CONSENT))).scalars().all()
        break
    assert len(rows) == 1
    p = json.loads(rows[0].payload_json)
    assert p["org_id"] == org_id
    assert p["privacy_policy_version"] == PRIVACY_POLICY_VERSION
    assert p["terms_version"] == TERMS_VERSION
    assert p["ip"] == "203.0.113.7"
    assert p["user_agent"] == "pytest-UA"
    assert p["consent_at_utc"].endswith("+00:00")
    # Desk notification has no PII
    blob = " ".join(t + b for t, b in pushes)
    assert "titular@legal.test" not in blob and "Ana Titular" not in blob and "Titular SAS" not in blob
    assert org_id in blob


async def _approved(c):
    desk = await c.post("/api/v1/auth/login", json={"token": "desk-secret"})
    dh = {"Authorization": f"Bearer {desk.json()['token']}"}
    r = await c.post("/api/v1/auth/companies", headers=dh, json=SIGNUP)
    assert r.status_code == 200, r.text  # desk alta does not need checkboxes
    return dh, r.json()["organization"]["id"]


@pytest.mark.asyncio
async def test_password_reset_alert_has_no_pii(pushes):
    async with await _client() as c:
        dh, org_id = await _approved(c)
        f = await c.post("/api/v1/auth/password/forgot", json={"email": SIGNUP["email"]})
        assert f.status_code == 200
        rid = f.json()["request_id"]
        # Flow still works: desk reads code from panel, client resets
        code = (await c.get("/api/v1/auth/password/resets", headers=dh)).json()["items"][0]["code"]
        ok = await c.post(
            "/api/v1/auth/password/reset",
            json={"email": SIGNUP["email"], "code": code, "new_password": "nueva12345", "request_id": rid},
        )
        assert ok.status_code == 200, ok.text
    reset_msgs = [b for t, b in pushes if "contraseña" in t.lower()]
    assert len(reset_msgs) == 1
    msg = reset_msgs[0]
    for pii in (SIGNUP["email"], SIGNUP["full_name"], SIGNUP["org_name"], code):
        assert pii not in msg
    assert rid in msg and org_id in msg and "panel" in msg


@pytest.mark.asyncio
async def test_data_request_endpoint(pushes):
    async with await _client() as c:
        dh, org_id = await _approved(c)
        await c.post("/api/v1/auth/logout")
        login = await c.post("/api/v1/auth/company/login", json={"email": SIGNUP["email"], "password": SIGNUP["password"]})
        assert login.status_code == 200, login.text
        ch = {"Authorization": f"Bearer {login.json()['token']}"}
        bad = await c.post("/api/v1/auth/data-requests", headers=ch, json={"kind": "borrar-todo"})
        assert bad.status_code == 422
        r = await c.post("/api/v1/auth/data-requests", headers=ch, json={"kind": "supresion", "detail": "Por favor"})
        assert r.status_code == 200, r.text
        rid = r.json()["request_id"]
        # Desk can list; desk itself cannot file one
        items = (await c.get("/api/v1/auth/data-requests", headers=dh)).json()["items"]
        assert any(i["request_id"] == rid and i["org_id"] == org_id and i["kind"] == "supresion" for i in items)
        assert (await c.post("/api/v1/auth/data-requests", headers=dh, json={"kind": "acceso"})).status_code == 403
        # Nothing auto-deleted: client can still log in
        again = await c.post("/api/v1/auth/company/login", json={"email": SIGNUP["email"], "password": SIGNUP["password"]})
        assert again.status_code == 200
    msg = [b for t, b in pushes if "habeas" in t.lower()][0]
    assert rid in msg and SIGNUP["email"] not in msg and SIGNUP["full_name"] not in msg
