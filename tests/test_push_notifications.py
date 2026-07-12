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
        assert result == {"telegram": False, "webhook": False}


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
    push.notify_alert = AsyncMock(return_value={"telegram": True, "webhook": False})

    service = AlertService(repo, cooldown_hours=24, push=push)
    result = await service.emit(saved_alert)

    assert result is not None
    push.notify_alert.assert_called_once_with(saved_alert)
