"""Replica lease + idempotent client_order_id for Strategy A PAPER cycles.

Survives FastAPI Cloud scale-to-zero and multiple replicas: lock lives in DB
(pg_advisory_lock when Postgres, else an expiring ops-flag lease).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

FLAG_LEASE = "crypto_strategy_a_lease"
LEASE_TTL_SEC = 180
ADVISORY_KEY = 1_340_090_01


def replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.uname().nodename or "unknown"
    return f"{host}:{os.getpid()}"


def idempotency_key(
    symbol: str,
    candle_open: datetime | str | None,
    action: str,
    attempt: int = 1,
) -> str:
    """client_order_id ≤ 48: sa9-{sym}-{YYYYMMDDHH}-{action}-{attempt}."""
    from services.order_idempotency import build_client_order_id, cycle_key_candle

    cycle = "na"
    if candle_open is not None:
        try:
            cycle = cycle_key_candle(candle_open)
        except Exception:
            cycle = "na"
    return build_client_order_id("sa9", symbol, action, cycle, attempt)


async def allocate_sa9_client_order_id(
    flags: Any,
    symbol: str,
    action: str,
    candle_open: datetime | str | None,
    *,
    broker: Any = None,
) -> tuple[str, int]:
    from services.order_idempotency import (
        attempt_slot,
        bump_attempt,
        cycle_key_candle,
        is_dead_retryable_status,
        read_attempt,
    )

    cycle = cycle_key_candle(candle_open) if candle_open is not None else "na"
    slot = attempt_slot(symbol, action, cycle)
    attempt = await read_attempt(flags, slot)
    cid = idempotency_key(symbol, candle_open, action, attempt=attempt)
    if broker is not None:
        getter = getattr(broker, "get_order_by_client_order_id", None)
        if getter is not None:
            try:
                existing = await getter(cid)
            except Exception:
                existing = None
            status = ""
            if isinstance(existing, dict):
                status = str(existing.get("status") or "")
            elif existing is not None:
                status = str(getattr(existing, "status", "") or "")
            if existing is not None and is_dead_retryable_status(status):
                attempt = await bump_attempt(flags, slot)
                cid = idempotency_key(symbol, candle_open, action, attempt=attempt)
    return cid, attempt


def pd_ts(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def try_pg_advisory_lock(session: Any, key: int = ADVISORY_KEY) -> bool | None:
    """True/False on Postgres; None if the dialect has no advisory locks."""
    if session is None:
        return None
    bind = getattr(session, "bind", None) or getattr(session, "get_bind", lambda: None)()
    url = str(getattr(bind, "url", "") or "")
    if "postgres" not in url and "postgresql" not in url:
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "")
        if dialect not in {"postgresql", "postgres"}:
            return None
    try:
        from sqlalchemy import text

        result = await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": int(key)})
        row = result.scalar()
        return bool(row)
    except Exception as exc:
        logger.warning("crypto_a.advisory_lock_failed", error=str(exc))
        return None


async def release_pg_advisory_lock(session: Any, key: int = ADVISORY_KEY) -> None:
    try:
        from sqlalchemy import text

        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
    except Exception:
        pass


async def acquire_cycle_lease(
    flags: Any,
    *,
    owner: str,
    now: datetime | None = None,
    ttl_sec: int = LEASE_TTL_SEC,
    session: Any = None,
) -> tuple[bool, dict[str, Any]]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    pg = await try_pg_advisory_lock(session)
    if pg is True:
        lease = {
            "owner": owner,
            "replica_id": owner,
            "expires_at": (clock + timedelta(seconds=ttl_sec)).isoformat(),
            "backend": "pg_advisory_lock",
        }
        if flags is not None:
            await flags.set_json(FLAG_LEASE, lease)
        return True, lease
    if pg is False:
        cur = await flags.get_json(FLAG_LEASE) if flags is not None else {}
        return False, cur or {"owner": "other", "backend": "pg_advisory_lock"}

    cur = await flags.get_json(FLAG_LEASE) if flags is not None else {}
    exp_raw = cur.get("expires_at")
    exp = None
    if exp_raw:
        try:
            exp = pd_ts(exp_raw)
        except Exception:
            exp = None
    if cur.get("owner") and exp and exp > clock and cur.get("owner") != owner:
        return False, cur
    lease = {
        "owner": owner,
        "replica_id": owner,
        "expires_at": (clock + timedelta(seconds=ttl_sec)).isoformat(),
        "backend": "ops_flag_lease",
    }
    if flags is not None:
        await flags.set_json(FLAG_LEASE, lease)
        again = await flags.get_json(FLAG_LEASE)
        if again.get("owner") and again.get("owner") != owner:
            exp2 = None
            try:
                exp2 = pd_ts(again.get("expires_at"))
            except Exception:
                exp2 = None
            if exp2 and exp2 > clock:
                return False, again
    return True, lease


async def release_cycle_lease(flags: Any, owner: str, session: Any = None) -> None:
    await release_pg_advisory_lock(session)
    if flags is None:
        return
    try:
        cur = await flags.get_json(FLAG_LEASE)
        if cur.get("owner") == owner:
            cur["released_at"] = datetime.now(timezone.utc).isoformat()
            cur["owner"] = None
            await flags.set_json(FLAG_LEASE, cur)
    except Exception:
        pass
