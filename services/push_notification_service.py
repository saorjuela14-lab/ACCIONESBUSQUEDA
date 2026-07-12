"""Push alerts via Telegram and optional webhook."""

from __future__ import annotations

import httpx

from config.settings import get_settings
from domain.entities import Alert
from domain.enums import AlertSeverity
from utils.logging import get_logger

logger = get_logger(__name__)

_SEVERITY_EMOJI = {
    AlertSeverity.CRITICAL: "🔴",
    AlertSeverity.HIGH: "🟠",
    AlertSeverity.MEDIUM: "🟡",
    AlertSeverity.LOW: "🟢",
}


class PushNotificationService:
    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def telegram_configured(self) -> bool:
        return bool(
            self._settings.telegram_alerts_enabled
            and self._settings.telegram_bot_token
            and self._settings.telegram_chat_id
        )

    @property
    def webhook_configured(self) -> bool:
        return bool(self._settings.alert_webhook_url)

    @property
    def any_channel_configured(self) -> bool:
        return self.telegram_configured or self.webhook_configured

    def status(self) -> dict[str, bool]:
        return {
            "telegram": self.telegram_configured,
            "webhook": self.webhook_configured,
            "enabled": self.any_channel_configured,
        }

    async def notify_alert(self, alert: Alert) -> dict[str, bool]:
        """Send alert to all configured channels. Never raises."""
        results = {"telegram": False, "webhook": False}
        text = self._format_alert(alert)

        if self.telegram_configured:
            results["telegram"] = await self._send_telegram(text)
        if self.webhook_configured:
            results["webhook"] = await self._send_webhook(alert, text)

        return results

    async def notify_message(self, title: str, body: str) -> dict[str, bool]:
        """Generic push (e.g. daily trade summary)."""
        text = f"<b>{title}</b>\n\n{body}"
        results = {"telegram": False, "webhook": False}

        if self.telegram_configured:
            results["telegram"] = await self._send_telegram(text)
        if self.webhook_configured:
            results["webhook"] = await self._send_webhook_payload(
                {"type": "message", "title": title, "body": body}
            )
        return results

    def _format_alert(self, alert: Alert) -> str:
        emoji = _SEVERITY_EMOJI.get(alert.severity, "📢")
        sev = alert.severity.value.upper()
        return (
            f"{emoji} <b>Alerta {sev}</b> — {alert.ticker}\n"
            f"<b>{alert.title}</b>\n"
            f"{alert.description[:800]}"
        )

    async def _send_telegram(self, html_text: str) -> bool:
        token = self._settings.telegram_bot_token
        chat_id = self._settings.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": html_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code != 200:
                    logger.warning(
                        "push.telegram.failed",
                        status=response.status_code,
                        detail=response.text[:200],
                    )
                    return False
                return True
        except Exception as exc:
            logger.warning("push.telegram.error", error=str(exc))
            return False

    async def _send_webhook(self, alert: Alert, text: str) -> bool:
        payload = {
            "type": "alert",
            "ticker": alert.ticker,
            "severity": alert.severity.value,
            "alert_type": alert.alert_type.value,
            "title": alert.title,
            "description": alert.description,
            "text": text.replace("<b>", "").replace("</b>", ""),
        }
        return await self._send_webhook_payload(payload)

    async def _send_webhook_payload(self, payload: dict) -> bool:
        url = self._settings.alert_webhook_url
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code >= 400:
                    logger.warning(
                        "push.webhook.failed",
                        status=response.status_code,
                        detail=response.text[:200],
                    )
                    return False
                return True
        except Exception as exc:
            logger.warning("push.webhook.error", error=str(exc))
            return False
