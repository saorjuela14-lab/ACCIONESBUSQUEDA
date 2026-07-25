"""Tests for push notification service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType
from services.push_notification_service import PushNotificationService


def _settings(**kwargs):
    defaults = {
        "telegram_alerts_enabled": True,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "alert_webhook_url": "",
        "whatsapp_enabled": True,
        "whatsapp_provider": "auto",
        "whatsapp_phone": "",
        "whatsapp_api_key": "",
        "whatsapp_token": "",
        "whatsapp_phone_number_id": "",
        "whatsapp_to": "",
        "whatsapp_api_version": "v21.0",
        "whatsapp_template_name": "",
        "whatsapp_template_lang": "es",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_whatsapp_from": "",
        "whatsapp_briefing_enabled": True,
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


@pytest.mark.asyncio
async def test_push_not_configured():
    with patch("services.push_notification_service.get_settings", return_value=_settings()):
        svc = PushNotificationService()
        assert not svc.any_channel_configured
        result = await svc.notify_alert(Alert(
            ticker="AAPL",
            alert_type=AlertType.BREAKOUT,
            severity=AlertSeverity.HIGH,
            title="Test",
            description="Body",
        ))
        assert result == {"telegram": False, "whatsapp": False, "webhook": False}


@pytest.mark.asyncio
async def test_push_telegram_success():
    with patch("services.push_notification_service.get_settings", return_value=_settings(
        telegram_bot_token="bot123",
        telegram_chat_id="999",
    )):
        svc = PushNotificationService()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.push_notification_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc.notify_alert(Alert(
                ticker="NVDA",
                alert_type=AlertType.BREAKOUT,
                severity=AlertSeverity.CRITICAL,
                title="Movimiento",
                description="Subió 5%",
            ))

        assert result["telegram"] is True
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_push_whatsapp_callmebot():
    with patch("services.push_notification_service.get_settings", return_value=_settings(
        whatsapp_phone="573001112233",
        whatsapp_api_key="key99",
    )):
        svc = PushNotificationService()
        assert svc.whatsapp_configured
        assert svc.status()["whatsapp_provider"] == "callmebot"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.push_notification_service.httpx.AsyncClient", return_value=mock_client):
            ok = await svc.notify_whatsapp_plain("Hola NexBuy")
        assert ok is True
        mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_push_whatsapp_meta():
    with patch("services.push_notification_service.get_settings", return_value=_settings(
        whatsapp_token="EAAxxx",
        whatsapp_phone_number_id="123456",
        whatsapp_to="573001112233",
        whatsapp_provider="meta",
    )):
        svc = PushNotificationService()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.push_notification_service.httpx.AsyncClient", return_value=mock_client):
            ok = await svc.notify_whatsapp_plain("Status apertura")
        assert ok is True
        args, kwargs = mock_client.post.call_args
        assert "graph.facebook.com" in args[0]
        assert kwargs["json"]["type"] == "text"


@pytest.mark.asyncio
async def test_alert_service_triggers_push_on_emit():
    from services.alert_service import AlertService

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    saved_alert = Alert(
        ticker="AAPL",
        alert_type=AlertType.BREAKOUT,
        severity=AlertSeverity.HIGH,
        title="test",
        description="test",
    )
    repo = MagicMock()
    repo._session = session
    repo.save = AsyncMock(return_value=saved_alert)

    push = AsyncMock()
    push.any_channel_configured = True
    push.notify_alert = AsyncMock(return_value={"telegram": True, "whatsapp": False, "webhook": False})

    service = AlertService(repo, cooldown_hours=24, push=push)
    result = await service.emit(saved_alert)

    assert result is not None
    push.notify_alert.assert_called_once_with(saved_alert)
