"""User/org WhatsApp number for alert delivery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apis.app import create_app
from config.settings import get_settings
from database.engine import init_db
from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType
from services.alert_service import AlertService
from services.company_auth_service import CompanyAuthService
from services.push_notification_service import PushNotificationService


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    db = tmp_path / "notify.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "desk-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_BRIEFING_ENABLED", "false")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_EMAIL", "")
    monkeypatch.setenv("COMPANY_BOOTSTRAP_PASSWORD", "")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_PHONE", "")
    monkeypatch.setenv("WHATSAPP_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_save_and_load_company_notify_phone():
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
        assert created.status_code == 200, created.text

        login = await client.post(
            "/api/v1/auth/company/login",
            json={"email": "ops@acme.test", "password": "segura1234"},
        )
        tok = login.json()["token"]

        bad = await client.patch(
            "/api/v1/auth/me/notify",
            headers={"Authorization": f"Bearer {tok}"},
            json={"phone": "123"},
        )
        assert bad.status_code == 400

        saved = await client.patch(
            "/api/v1/auth/me/notify",
            headers={"Authorization": f"Bearer {tok}"},
            json={"phone": "+573001112233", "whatsapp_api_key": "key-acme"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["notify_phone"] in ("+573001112233", "573001112233")
        assert saved.json()["has_whatsapp_key"] is True

        me = await client.get(
            "/api/v1/auth/me/notify", headers={"Authorization": f"Bearer {tok}"}
        )
        assert me.status_code == 200
        assert "573001112233" in me.json()["notify_phone"]


@pytest.mark.asyncio
async def test_desk_can_save_notify_phone():
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.post("/api/v1/auth/login", json={"token": "desk-secret"})
        desk_tok = desk.json()["token"]
        saved = await client.patch(
            "/api/v1/auth/me/notify",
            headers={"Authorization": f"Bearer {desk_tok}"},
            json={"phone": "+12025550100", "whatsapp_api_key": "desk-key"},
        )
        assert saved.status_code == 200, saved.text
        assert "12025550100" in saved.json()["notify_phone"]


@pytest.mark.asyncio
async def test_alert_emit_fans_out_to_saved_phone():
    await init_db()
    from database.engine import _session_factory
    from database.repositories.alert_repository import AlertRepository

    assert _session_factory is not None
    async with _session_factory() as session:
        svc = CompanyAuthService(session)
        org, user, _ = await svc.create_company(
            org_name="Beta",
            email="b@beta.test",
            password="segura1234",
            full_name="Beta",
        )
        await svc.update_notify_prefs(
            user_id=user.id,
            org_id=org.id,
            role="company_admin",
            phone="+573009998887",
            whatsapp_api_key="user-key",
        )
        org_id = org.id

    async with _session_factory() as session:
        push = MagicMock()
        push.any_channel_configured = False
        push.notify_alert = AsyncMock(
            return_value={"telegram": False, "whatsapp": False, "webhook": False}
        )
        push.notify_whatsapp_targets = AsyncMock(return_value={"9998887": True})
        push._format_alert = PushNotificationService._format_alert.__get__(
            PushNotificationService(), PushNotificationService
        )

        alerts = AlertService(AlertRepository(session), cooldown_hours=0, push=push)
        saved = await alerts.emit(
            Alert(
                ticker="NVDA",
                alert_type=AlertType.BREAKOUT,
                severity=AlertSeverity.HIGH,
                title="Sube",
                description="test",
                org_id=org_id,
            )
        )
        assert saved is not None
        push.notify_whatsapp_targets.assert_awaited()
        args = push.notify_whatsapp_targets.await_args
        targets = args.args[1]
        assert any("573009998887" in (t.get("phone") or "") for t in targets)


@pytest.mark.asyncio
async def test_whatsapp_plain_uses_override_callmebot_key():
    settings = MagicMock(
        telegram_alerts_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="",
        whatsapp_enabled=True,
        whatsapp_provider="auto",
        whatsapp_phone="",
        whatsapp_api_key="",
        whatsapp_token="",
        whatsapp_phone_number_id="",
        whatsapp_to="",
        whatsapp_api_version="v21.0",
        whatsapp_template_name="",
        whatsapp_template_lang="es",
        twilio_account_sid="",
        twilio_auth_token="",
        twilio_whatsapp_from="",
        whatsapp_briefing_enabled=True,
    )
    with patch("services.push_notification_service.get_settings", return_value=settings):
        svc = PushNotificationService()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "Message queued"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("services.push_notification_service.httpx.AsyncClient", return_value=mock_client):
            ok = await svc.notify_whatsapp_plain(
                "hola", to_phone="+573001112233", callmebot_key="abc"
            )
        assert ok is True
        mock_client.get.assert_awaited()
        url = mock_client.get.await_args.args[0]
        assert "573001112233" in url
        assert "apikey=abc" in url
