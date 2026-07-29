"""Push alerts via Telegram, WhatsApp, and optional webhook."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

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

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    plain = _HTML_TAG_RE.sub("", text or "")
    return html.unescape(plain).strip()


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
    def whatsapp_configured(self) -> bool:
        if not getattr(self._settings, "whatsapp_enabled", True):
            return False
        provider = self._resolved_whatsapp_provider()
        return provider is not None

    @property
    def any_channel_configured(self) -> bool:
        return (
            self.telegram_configured
            or self.webhook_configured
            or self.whatsapp_configured
        )

    def status(self) -> dict[str, bool | str | None]:
        return {
            "telegram": self.telegram_configured,
            "whatsapp": self.whatsapp_configured,
            "whatsapp_provider": self._resolved_whatsapp_provider(),
            "webhook": self.webhook_configured,
            "enabled": self.any_channel_configured,
            "briefing_enabled": bool(
                getattr(self._settings, "whatsapp_briefing_enabled", True)
            ),
        }

    async def notify_alert(self, alert: Alert) -> dict[str, bool]:
        """Send alert to all configured channels. Never raises."""
        results = {"telegram": False, "whatsapp": False, "webhook": False}
        text = self._format_alert(alert)

        if self.telegram_configured:
            results["telegram"] = await self._send_telegram(text)
        if self.whatsapp_configured:
            results["whatsapp"] = await self.notify_whatsapp_plain(_strip_html(text))
        if self.webhook_configured:
            results["webhook"] = await self._send_webhook(alert, text)

        return results

    async def notify_message(
        self,
        title: str,
        body: str,
        *,
        prefer_plain: bool = False,
        channels: tuple[str, ...] = ("telegram", "whatsapp", "webhook"),
    ) -> dict[str, bool]:
        """Generic push (alerts, daily trades, open/close briefing)."""
        html_text = f"<b>{title}</b>\n\n{body}"
        plain = f"{title}\n\n{body}" if prefer_plain else _strip_html(html_text)
        results = {"telegram": False, "whatsapp": False, "webhook": False}

        if "telegram" in channels and self.telegram_configured:
            results["telegram"] = await self._send_telegram(
                plain if prefer_plain else html_text
            )
        if "whatsapp" in channels and self.whatsapp_configured:
            results["whatsapp"] = await self.notify_whatsapp_plain(plain)
        if "webhook" in channels and self.webhook_configured:
            results["webhook"] = await self._send_webhook_payload(
                {"type": "message", "title": title, "body": body}
            )
        return results

    async def notify_whatsapp_plain(self, text: str) -> bool:
        """Send plain text to WhatsApp via the configured provider."""
        provider = self._resolved_whatsapp_provider()
        if not provider:
            return False
        text = (text or "")[:3900]
        try:
            if provider == "callmebot":
                return await self._send_whatsapp_callmebot(text)
            if provider == "meta":
                return await self._send_whatsapp_meta(text)
            if provider == "twilio":
                return await self._send_whatsapp_twilio(text)
        except Exception as exc:
            logger.warning("push.whatsapp.error", provider=provider, error=str(exc))
            return False
        return False

    def _resolved_whatsapp_provider(self) -> str | None:
        s = self._settings
        requested = (getattr(s, "whatsapp_provider", "auto") or "auto").lower().strip()
        if requested == "callmebot" and s.whatsapp_phone and s.whatsapp_api_key:
            return "callmebot"
        if requested == "meta" and s.whatsapp_token and s.whatsapp_phone_number_id and s.whatsapp_to:
            return "meta"
        if (
            requested == "twilio"
            and s.twilio_account_sid
            and s.twilio_auth_token
            and s.twilio_whatsapp_from
            and s.whatsapp_to
        ):
            return "twilio"
        if requested == "auto":
            if s.whatsapp_phone and s.whatsapp_api_key:
                return "callmebot"
            if s.whatsapp_token and s.whatsapp_phone_number_id and s.whatsapp_to:
                return "meta"
            if (
                s.twilio_account_sid
                and s.twilio_auth_token
                and s.twilio_whatsapp_from
                and s.whatsapp_to
            ):
                return "twilio"
        return None

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
        # Telegram HTML parse fails on plain-only sometimes — send as HTML if tags present
        parse_mode = "HTML" if "<b>" in html_text or "<i>" in html_text else None
        payload: dict = {
            "chat_id": chat_id,
            "text": html_text[:4000],
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
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

    async def _send_whatsapp_callmebot(self, text: str) -> bool:
        phone = re.sub(r"[^\d]", "", self._settings.whatsapp_phone or "")
        key = self._settings.whatsapp_api_key
        url = (
            "https://api.callmebot.com/whatsapp.php"
            f"?phone={phone}&text={quote(text)}&apikey={quote(key)}"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            body = (response.text or "")[:500]
            body_l = body.lower()
            if response.status_code >= 400:
                logger.warning(
                    "push.whatsapp.callmebot_failed",
                    status=response.status_code,
                    detail=body[:200],
                )
                return False
            # CallMeBot often returns 200 HTML; require queued/ack and reject error pages
            bad = ("invalid", "not activated", "error", "blocked", "wrong apikey", "api key")
            if any(b in body_l for b in bad) and "queued" not in body_l:
                logger.warning(
                    "push.whatsapp.callmebot_rejected",
                    status=response.status_code,
                    detail=body[:200],
                )
                return False
            logger.info(
                "push.whatsapp.callmebot_ok",
                status=response.status_code,
                queued="queued" in body_l,
            )
            return True

    async def _send_whatsapp_meta(self, text: str) -> bool:
        s = self._settings
        version = getattr(s, "whatsapp_api_version", "v21.0") or "v21.0"
        url = (
            f"https://graph.facebook.com/{version}/"
            f"{s.whatsapp_phone_number_id}/messages"
        )
        headers = {
            "Authorization": f"Bearer {s.whatsapp_token}",
            "Content-Type": "application/json",
        }
        to = re.sub(r"[^\d]", "", s.whatsapp_to or "")
        template = (getattr(s, "whatsapp_template_name", "") or "").strip()
        if template:
            # Utility template: body params = chunks of the briefing
            chunks = [text[i : i + 900] for i in range(0, min(len(text), 2700), 900)] or [text[:900]]
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": getattr(s, "whatsapp_template_lang", "es") or "es"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": c[:1024]} for c in chunks[:3]
                            ],
                        }
                    ],
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text[:4096]},
            }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.warning(
                    "push.whatsapp.meta_failed",
                    status=response.status_code,
                    detail=response.text[:300],
                )
                return False
            return True

    async def _send_whatsapp_twilio(self, text: str) -> bool:
        s = self._settings
        sid = s.twilio_account_sid
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        to = s.whatsapp_to
        if not to.startswith("whatsapp:"):
            digits = re.sub(r"[^\d+]", "", to)
            if not digits.startswith("+"):
                digits = "+" + digits.lstrip("+")
            to = f"whatsapp:{digits}"
        data = {
            "From": s.twilio_whatsapp_from,
            "To": to,
            "Body": text[:1600],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                data=data,
                auth=(sid, s.twilio_auth_token),
            )
            if response.status_code >= 400:
                logger.warning(
                    "push.whatsapp.twilio_failed",
                    status=response.status_code,
                    detail=response.text[:300],
                )
                return False
            return True

    async def _send_webhook(self, alert: Alert, text: str) -> bool:
        payload = {
            "type": "alert",
            "ticker": alert.ticker,
            "severity": alert.severity.value,
            "alert_type": alert.alert_type.value,
            "title": alert.title,
            "description": alert.description,
            "text": _strip_html(text),
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
