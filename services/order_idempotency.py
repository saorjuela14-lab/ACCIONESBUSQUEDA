"""Order idempotency + live-stop detection (stocks LIVE and paper crypto A).

client_order_id = prefix-symbol-cycle-side-attempt. Attempt lives in the ops-flag DB.
HTTP 422 / 40010001 is a duplicate, never a new order. Timeouts look up the same id
before retrying. A 422 for any other reason is not a duplicate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from utils.logging import get_logger

logger = get_logger(__name__)

ALPACA_DUPLICATE_COID_CODE = 40_010_001
ALPACA_INSUFFICIENT_QTY_CODE = 40_310_000
DUPLICATE_COID_PHRASE = "client_order_id must be unique"
FLAG_ATTEMPTS = "order_client_id_attempts"
WORKING_ORDER_STATUSES = frozenset(
    {
        "new",
        "held",
        "accepted",
        "pending_new",
        "partially_filled",
        "pending_replace",
        "accepted_for_bidding",
    }
)
DEAD_RETRYABLE_STATUSES = frozenset({"canceled", "cancelled", "rejected", "expired"})
STALE_STOP_STATUSES = DEAD_RETRYABLE_STATUSES | frozenset(
    {"filled", "replaced", "done_for_day"}
)


class AttemptUnavailable(Exception):
    """Read/write of the attempt counter failed. Fail closed — never invent 1."""


def _sym(symbol: str) -> str:
    return (symbol or "X").upper().replace("/", "").replace("-", "")[:8]


def cycle_key_date(when: datetime | None = None) -> str:
    clock = when or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.strftime("%Y%m%d")


def cycle_key_candle(when: datetime | str | None) -> str:
    if when is None:
        return cycle_key_date()
    if isinstance(when, datetime):
        dt = when
    else:
        try:
            dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError:
            return "".join(c for c in str(when) if c.isalnum())[:12] or cycle_key_date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y%m%d%H")


def attempt_slot(symbol: str, side: str, cycle: str) -> str:
    act = "".join(c for c in (side or "x").lower() if c.isalnum())[:10] or "x"
    return f"{_sym(symbol)}:{act}:{cycle}"


def build_client_order_id(
    prefix: str,
    symbol: str,
    side: str,
    cycle: str,
    attempt: int,
) -> str:
    """prefix-SYM-cycle-side-attempt, ≤48 chars."""
    act = "".join(c for c in (side or "x").lower() if c.isalnum())[:10] or "x"
    n = max(1, int(attempt or 1))
    return f"{prefix}-{_sym(symbol)}-{cycle}-{act}-{n}"[:48]


def alpaca_error_code(exc: BaseException) -> int | None:
    raw = getattr(exc, "alpaca_code", None)
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    if isinstance(body, dict) and body.get("code") is not None:
        try:
            return int(body.get("code"))
        except (TypeError, ValueError):
            return None
    return None


def alpaca_http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "alpaca_status", None) or getattr(exc, "status_code", None)
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return int(getattr(response, "status_code", 0) or 0) or None
        except (TypeError, ValueError):
            return None
    return None


def _alpaca_error_text(exc: BaseException) -> str:
    parts = [str(exc or "")]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            parts.append(str(body.get("message") or ""))
            parts.append(str(body.get("error") or ""))
    return " ".join(parts).lower()


def is_duplicate_client_order_id_error(exc: BaseException) -> bool:
    """True only for HTTP 422 whose message contains ``client_order_id must be unique``.

    40010001 is a generic Alpaca validation code and is not sufficient by itself.
    """
    if alpaca_http_status(exc) != 422:
        return False
    return DUPLICATE_COID_PHRASE in _alpaca_error_text(exc)


def is_unrelated_422(exc: BaseException) -> bool:
    return alpaca_http_status(exc) == 422 and not is_duplicate_client_order_id_error(exc)


def is_timeout_or_network(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            ConnectionError,
            TimeoutError,
        ),
    ):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc or "").lower()
    if "timeout" in name or "timeout" in msg:
        return True
    if "connect" in name and "http" not in name:
        return True
    if "network" in name or "connection reset" in msg or "temporarily unavailable" in msg:
        return True
    return False


def is_insufficient_qty_error(exc: BaseException | str | None) -> bool:
    if exc is not None and not isinstance(exc, str):
        if alpaca_error_code(exc) == ALPACA_INSUFFICIENT_QTY_CODE:
            return True
    text = str(exc or "").lower()
    return (
        "insufficient qty" in text
        or "insufficient quantity" in text
        or "40310000" in text
    )


def is_working_status(status: str | None) -> bool:
    return (status or "").strip().lower() in WORKING_ORDER_STATUSES


def is_dead_retryable_status(status: str | None) -> bool:
    return (status or "").strip().lower() in DEAD_RETRYABLE_STATUSES


def is_stale_stop_status(status: str | None) -> bool:
    """Filled / replaced / done_for_day / canceled / expired — not a live stop."""
    return (status or "").strip().lower() in STALE_STOP_STATUSES


def is_protective_stop_request(req: Any) -> bool:
    otype = str(getattr(req, "order_type", None) or getattr(req, "type", "") or "").lower()
    return otype in {"stop", "stop_limit"}


def order_is_live_stop(order: Any) -> bool:
    """Protective sell stop in a live (incl. held) state."""
    if order is None:
        return False
    if isinstance(order, dict):
        side = str(order.get("side") or "").lower()
        otype = str(order.get("type") or order.get("order_type") or "").lower()
        status = str(order.get("status") or "").lower()
        raw = order
    else:
        side = str(getattr(order, "side", "") or "").lower()
        otype = str(getattr(order, "type", "") or "").lower()
        status = str(getattr(order, "status", "") or "").lower()
        raw = getattr(order, "raw", None) or {}
    if side and side != "sell":
        return False
    if not is_working_status(status):
        return False
    if otype in {"stop", "stop_limit"} or "stop" in otype:
        return True
    if isinstance(raw, dict) and (raw.get("stop_price") or raw.get("stop_loss")):
        return True
    return False


def _repo_session(flags: Any) -> Any:
    try:
        from database.repositories.ops_repository import OpsFlagRepository
    except Exception:
        return None
    if isinstance(flags, OpsFlagRepository):
        return flags._session
    return None


async def read_attempt(flags: Any, slot: str) -> int:
    if flags is None:
        raise AttemptUnavailable("flags_missing")
    session = _repo_session(flags)
    if session is not None:
        try:
            from sqlalchemy import text

            row = (
                await session.execute(
                    text("SELECT attempt FROM order_id_attempts WHERE slot = :s"),
                    {"s": slot},
                )
            ).first()
        except Exception as exc:
            logger.warning("order_id.attempt_read_failed", slot=slot, error=str(exc))
            raise AttemptUnavailable(str(exc)) from exc
        if not row:
            return 1
        try:
            return max(1, int(row[0] or 1))
        except (TypeError, ValueError) as exc:
            raise AttemptUnavailable("attempt_corrupt") from exc
    try:
        data = await flags.get_json(FLAG_ATTEMPTS)
    except Exception as exc:
        logger.warning("order_id.attempt_read_failed", slot=slot, error=str(exc))
        raise AttemptUnavailable(str(exc)) from exc
    try:
        n = int((data or {}).get(slot) or 1)
    except (TypeError, ValueError) as exc:
        raise AttemptUnavailable("attempt_corrupt") from exc
    return max(1, n)


async def persist_attempt(flags: Any, slot: str, attempt: int) -> int:
    n = max(1, int(attempt or 1))
    if flags is None:
        raise AttemptUnavailable("flags_missing")
    session = _repo_session(flags)
    if session is not None:
        try:
            from sqlalchemy import text

            dialect = str(getattr(getattr(session.get_bind(), "dialect", None), "name", "") or "")
            if dialect.startswith("postgres"):
                await session.execute(
                    text(
                        "INSERT INTO order_id_attempts (slot, attempt) VALUES (:s, :n) "
                        "ON CONFLICT (slot) DO UPDATE SET attempt = EXCLUDED.attempt"
                    ),
                    {"s": slot, "n": n},
                )
            else:
                await session.execute(
                    text(
                        "INSERT INTO order_id_attempts (slot, attempt) VALUES (:s, :n) "
                        "ON CONFLICT(slot) DO UPDATE SET attempt = excluded.attempt"
                    ),
                    {"s": slot, "n": n},
                )
            await session.commit()
        except Exception as exc:
            logger.warning("order_id.attempt_write_failed", slot=slot, error=str(exc))
            raise AttemptUnavailable(str(exc)) from exc
        return n
    try:
        data = dict(await flags.get_json(FLAG_ATTEMPTS) or {})
        data[slot] = n
        if len(data) > 400:
            extra = list(data.keys())[:-200]
            for key in extra:
                data.pop(key, None)
        await flags.set_json(FLAG_ATTEMPTS, data)
    except Exception as exc:
        logger.warning("order_id.attempt_write_failed", slot=slot, error=str(exc))
        raise AttemptUnavailable(str(exc)) from exc
    return n


async def bump_attempt(flags: Any, slot: str) -> int:
    session = _repo_session(flags)
    if session is not None:
        try:
            from sqlalchemy import text

            dialect = str(getattr(getattr(session.get_bind(), "dialect", None), "name", "") or "")
            if dialect.startswith("postgres"):
                sql = (
                    "INSERT INTO order_id_attempts (slot, attempt) VALUES (:s, 2) "
                    "ON CONFLICT (slot) DO UPDATE SET attempt = order_id_attempts.attempt + 1 "
                    "RETURNING attempt"
                )
            else:
                sql = (
                    "INSERT INTO order_id_attempts (slot, attempt) VALUES (:s, 2) "
                    "ON CONFLICT(slot) DO UPDATE SET attempt = order_id_attempts.attempt + 1 "
                    "RETURNING attempt"
                )
            row = (await session.execute(text(sql), {"s": slot})).first()
            await session.commit()
        except Exception as exc:
            logger.warning("order_id.attempt_bump_failed", slot=slot, error=str(exc))
            raise AttemptUnavailable(str(exc)) from exc
        if not row:
            raise AttemptUnavailable("attempt_bump_empty")
        return max(1, int(row[0]))
    current = await read_attempt(flags, slot)
    return await persist_attempt(flags, slot, current + 1)
