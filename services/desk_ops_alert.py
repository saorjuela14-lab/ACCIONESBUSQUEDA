"""Desk operational alerts (push / WhatsApp) with in-process dedupe.

Used for lease misses, uncertain orders, and stop-reconcile failures.
Same channel as mesa avisos: ``PushNotificationService.notify_message``.
"""

from __future__ import annotations

import time
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

KIND_LEASE_MISSES = "lease_misses"
KIND_ORDER_UNCERTAIN = "order_uncertain_no_retry"
KIND_STOP_RECHECK_EMPTY = "stop_recheck_empty"
KIND_STOP_NOT_LIVE = "stop_reconciled_not_live"

_TITLES = {
    KIND_LEASE_MISSES: "Desk lease: 2+ misses",
    KIND_ORDER_UNCERTAIN: "Orden incierta — no retry",
    KIND_STOP_RECHECK_EMPTY: "Stop: 2ª búsqueda vacía",
    KIND_STOP_NOT_LIVE: "Stop reconciliado no vivo",
}

DEDUPE_SECONDS = 15 * 60
_sent: dict[str, float] = {}


def reset_desk_ops_alert_dedupe() -> None:
    """Test helper."""
    _sent.clear()


def _dedupe_key(
    kind: str,
    *,
    owner: str | None,
    detail: str,
    dedupe_key: str | None = None,
) -> str:
    if dedupe_key:
        return str(dedupe_key)
    if kind == KIND_LEASE_MISSES:
        return f"{kind}|{owner or 'unknown'}"
    return f"{kind}|{owner or ''}|{(detail or '')[:80]}"


async def emit_desk_ops_alert(
    kind: str,
    *,
    detail: str = "",
    owner: str | None = None,
    title: str | None = None,
    force: bool = False,
    dedupe_key: str | None = None,
) -> bool:
    """Send a mesa alert. False when no channel is configured or send fails."""
    key = _dedupe_key(kind, owner=owner, detail=detail, dedupe_key=dedupe_key)
    now = time.monotonic()
    if not force:
        last = _sent.get(key, 0.0)
        if now - last < DEDUPE_SECONDS:
            logger.info("desk_ops_alert.suppressed", kind=kind, owner=owner)
            return False

    text_title = title or _TITLES.get(kind, kind)
    body = detail.strip() or kind
    if owner:
        body = f"owner={owner}\n{body}"
    logger.warning("desk_ops_alert.emit", kind=kind, owner=owner, detail=detail[:200])
    try:
        from services.push_notification_service import PushNotificationService

        push = PushNotificationService()
        if not push.any_channel_configured:
            logger.info("desk_ops_alert.no_channel", kind=kind)
            return False
        await push.notify_message(text_title, body)
    except Exception as exc:
        logger.warning("desk_ops_alert.failed", kind=kind, error=str(exc))
        return False
    _sent[key] = now
    return True


def alert_payload(kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, **extra}
