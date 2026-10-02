"""Replica lease + deterministic client_order_id for LIVE equity autopilot."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

FLAG_LEASE = "live_equity_autopilot_lease"
FLAG_ENTRY_LOCK = "live_entry_slot_lease"
LEASE_TTL_SEC = 180
ADVISORY_KEY = 1_350_090_01
ENTRY_ADVISORY_KEY = 1_350_090_02


def replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.uname().nodename or "unknown"
    return f"{host}:{os.getpid()}"


def live_client_order_id(symbol: str, action: str, when: datetime | None = None) -> str:
    """client_order_id ≤ 48: live-{sym}-{YYYYMMDD}-{action}."""
    raw = (symbol or "X").upper().replace("/", "").replace("-", "")[:8]
    clock = when or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    ts = clock.strftime("%Y%m%d")
    act = "".join(c for c in (action or "x").lower() if c.isalnum())[:10] or "x"
    return f"live-{raw}-{ts}-{act}"[:48]


def _ts(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def try_pg_advisory_lock(session: Any, key: int) -> bool | None:
    if session is None:
        return None
    bind = getattr(session, "bind", None) or getattr(session, "get_bind", lambda: None)()
    url = str(getattr(bind, "url", "") or "")
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "")
    if "postgres" not in url and dialect not in {"postgresql", "postgres"}:
        return None
    try:
        from sqlalchemy import text

        result = await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": int(key)})
        return bool(result.scalar())
    except Exception as exc:
        logger.warning("live_lock.advisory_failed", error=str(exc))
        return None


async def release_pg_advisory_lock(session: Any, key: int) -> None:
    try:
        from sqlalchemy import text

        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
    except Exception:
        pass


async def acquire_lease(
    flags: Any,
    *,
    owner: str,
    flag: str = FLAG_LEASE,
    advisory_key: int = ADVISORY_KEY,
    now: datetime | None = None,
    ttl_sec: int = LEASE_TTL_SEC,
    session: Any = None,
) -> tuple[bool, dict[str, Any]]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    pg = await try_pg_advisory_lock(session, advisory_key)
    if pg is True:
        lease = {
            "owner": owner,
            "expires_at": (clock + timedelta(seconds=ttl_sec)).isoformat(),
            "backend": "pg_advisory_lock",
        }
        if flags is not None:
            await flags.set_json(flag, lease)
        return True, lease
    if pg is False:
        cur = await flags.get_json(flag) if flags is not None else {}
        return False, cur or {"owner": "other", "backend": "pg_advisory_lock"}

    cur = await flags.get_json(flag) if flags is not None else {}
    exp = None
    if cur.get("expires_at"):
        try:
            exp = _ts(cur["expires_at"])
        except Exception:
            exp = None
    if cur.get("owner") and exp and exp > clock and cur.get("owner") != owner:
        return False, cur
    lease = {
        "owner": owner,
        "expires_at": (clock + timedelta(seconds=ttl_sec)).isoformat(),
        "backend": "ops_flag_lease",
    }
    if flags is not None:
        await flags.set_json(flag, lease)
    return True, lease


async def release_lease(
    flags: Any,
    owner: str,
    *,
    flag: str = FLAG_LEASE,
    advisory_key: int = ADVISORY_KEY,
    session: Any = None,
) -> None:
    await release_pg_advisory_lock(session, advisory_key)
    if flags is None:
        return
    try:
        cur = await flags.get_json(flag)
        if cur.get("owner") == owner:
            cur["owner"] = None
            cur["released_at"] = datetime.now(timezone.utc).isoformat()
            await flags.set_json(flag, cur)
    except Exception:
        pass
