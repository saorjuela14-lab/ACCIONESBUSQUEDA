"""Alpaca crypto order constraints (PAPER Strategy A).

Alpaca crypto accepts market, limit, stop_limit only — not stop, bracket, OCO,
or trailing. Strategy A does not send any stop-type order (software chandelier
at 4h close → market on next open). No emergency broker stop in this PR.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

# Types Alpaca documents for crypto. We still do not send stop_limit.
ALPACA_CRYPTO_TYPES = frozenset({"market", "limit", "stop_limit"})
ALLOWED_SEND_TYPES = frozenset({"market", "limit"})
STOP_LIKE_TYPES = frozenset({"stop", "stop_limit", "trailing_stop", "trailing_stop_limit", "trailing"})
STRIP_KEYS = frozenset(
    {
        "order_class",
        "stop_loss",
        "take_profit",
        "trail_price",
        "trail_percent",
        "legs",
    }
)
FLAG_ALERT = "crypto_strategy_a_broker_stop_alert"


class CryptoStopNotSupported(ValueError):
    """Raised when a crypto payload would send a stop/bracket/OCO/trailing order."""


def is_crypto_symbol(symbol: str | None) -> bool:
    s = (symbol or "").upper().replace(" ", "")
    if "/" in s:
        return s.endswith("/USD") or s.endswith("/USDT") or s.endswith("/USDC")
    return s.endswith("USD") and len(s) > 3


def is_stop_like(order: dict[str, Any] | None) -> bool:
    if not isinstance(order, dict):
        return False
    t = str(order.get("type") or "").lower()
    oc = str(order.get("order_class") or "").lower()
    if t in STOP_LIKE_TYPES:
        return True
    if oc in {"oto", "oco", "bracket", "trailing"}:
        return True
    if order.get("stop_loss") or order.get("take_profit"):
        return True
    if order.get("trail_price") is not None or order.get("trail_percent") is not None:
        return True
    return False


def sanitize_crypto_order(order: dict[str, Any]) -> dict[str, Any]:
    """Strip stop/bracket fields. Refuse stop-like types. Never enlarge the ticket."""
    out = {k: v for k, v in (order or {}).items() if k not in STRIP_KEYS}
    t = str(out.get("type") or "market").lower()
    if t in STOP_LIKE_TYPES or t not in ALLOWED_SEND_TYPES:
        raise CryptoStopNotSupported(
            f"Alpaca crypto: no se envían órdenes tipo {t!r} "
            "(solo market/limit; stop de software al cierre 4h)."
        )
    out["type"] = t
    return out


def broker_stop_none_payload() -> dict[str, str]:
    return {"broker_stop": "none"}


async def record_rejected_crypto_stop(
    session: Any | None,
    *,
    symbol: str,
    detail: str,
    raw: Any = None,
) -> dict[str, Any]:
    """Log + persist + optional push. Position must be marked broker_stop=none."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "detail": str(detail)[:800],
        "raw_status": (raw.get("status") if isinstance(raw, dict) else None),
        "broker_stop": "none",
        "paper": True,
    }
    logger.warning("crypto.a.broker_stop_rejected", **{k: v for k, v in payload.items() if k != "raw_status"})
    if session is not None:
        try:
            from database.repositories.ops_repository import OpsFlagRepository

            await OpsFlagRepository(session).set_json(FLAG_ALERT, payload)
        except Exception as exc:
            logger.warning("crypto.a.broker_stop_alert_flag_failed", error=str(exc))
    try:
        from services.push_notification_service import PushNotificationService

        push = PushNotificationService()
        if push.any_channel_configured:
            await push.notify_message(
                "PAPER crypto: stop rechazado",
                f"{symbol}: Alpaca rechazó una orden stop. broker_stop=none. "
                "Stop de software al cierre de cada vela 4h (sin stop de emergencia en broker).",
                channels=("telegram", "webhook"),
            )
    except Exception as exc:
        logger.warning("crypto.a.broker_stop_push_failed", error=str(exc))
    return payload
