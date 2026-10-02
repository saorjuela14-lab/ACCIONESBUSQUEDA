"""LIVE equity cycle lease + deterministic client_order_id.

The cycle lock is a DB row lease (``desk_leases.live_stocks``). Session advisory
locks are not used — they do not survive PgBouncer transaction-mode pooling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from services.db_lease import (
    DEFAULT_TTL_SECONDS,
    LEASE_LIVE_ENTRY,
    LEASE_LIVE_STOCKS,
    acquire_lease as acquire_row_lease,
    heartbeat_lease as heartbeat_row_lease,
    release_lease as release_row_lease,
    replica_id,
    run_owner,
    snapshot_lease,
)

FLAG_LEASE = "live_equity_autopilot_lease"
FLAG_ENTRY_LOCK = "live_entry_slot_lease"
LEASE_TTL_SEC = DEFAULT_TTL_SECONDS
ADVISORY_KEY = 1_350_090_01  # retained for call-site compat; unused
ENTRY_ADVISORY_KEY = 1_350_090_02

__all__ = [
    "ADVISORY_KEY",
    "ENTRY_ADVISORY_KEY",
    "FLAG_ENTRY_LOCK",
    "FLAG_LEASE",
    "LEASE_TTL_SEC",
    "acquire_lease",
    "allocate_live_client_order_id",
    "heartbeat_cycle_lease",
    "live_client_order_id",
    "release_lease",
    "replica_id",
    "run_owner",
    "snapshot_cycle_lease",
]


def _lease_name(flag: str | None) -> str:
    if flag == FLAG_ENTRY_LOCK:
        return LEASE_LIVE_ENTRY
    return LEASE_LIVE_STOCKS


def live_client_order_id(
    symbol: str,
    action: str,
    when: datetime | None = None,
    attempt: int = 1,
) -> str:
    """client_order_id ≤ 48: live-{sym}-{YYYYMMDD}-{action}-{attempt}."""
    from services.order_idempotency import build_client_order_id, cycle_key_date

    return build_client_order_id("live", symbol, action, cycle_key_date(when), attempt)


async def allocate_live_client_order_id(
    flags: Any,
    symbol: str,
    action: str,
    *,
    when: datetime | None = None,
    broker: Any = None,
) -> tuple[str, int]:
    """Read the persisted attempt (DB), bump if the last id was canceled/rejected."""
    from services.order_idempotency import (
        AttemptUnavailable,
        attempt_slot,
        bump_attempt,
        cycle_key_date,
        is_dead_retryable_status,
        order_is_live_stop,
        read_attempt,
    )

    cycle = cycle_key_date(when)
    slot = attempt_slot(symbol, action, cycle)
    try:
        attempt = await read_attempt(flags, slot)
    except AttemptUnavailable:
        raise
    cid = live_client_order_id(symbol, action, when, attempt=attempt)
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
            should_bump = False
            if existing is not None and is_dead_retryable_status(status):
                should_bump = True
            if existing is not None and (action or "").lower() == "stop":
                if not order_is_live_stop(existing):
                    should_bump = True
            if should_bump:
                attempt = await bump_attempt(flags, slot)
                cid = live_client_order_id(symbol, action, when, attempt=attempt)
    return cid, attempt


async def acquire_lease(
    flags: Any,
    *,
    owner: str,
    flag: str = FLAG_LEASE,
    advisory_key: int = ADVISORY_KEY,
    now: datetime | None = None,
    ttl_sec: int = LEASE_TTL_SEC,
    session: AsyncSession | None = None,
) -> tuple[bool, dict[str, Any]]:
    del flags, advisory_key, now
    if session is None:
        return False, {"error": "session_required", "backend": "desk_lease"}
    snap = await acquire_row_lease(
        session, name=_lease_name(flag), owner=owner, ttl_seconds=ttl_sec
    )
    payload = snap.as_dict()
    payload["backend"] = "desk_lease"
    return snap.acquired, payload


async def heartbeat_cycle_lease(
    session: AsyncSession,
    *,
    owner: str,
    flag: str = FLAG_LEASE,
    ttl_seconds: int = LEASE_TTL_SEC,
) -> bool:
    return await heartbeat_row_lease(
        session, name=_lease_name(flag), owner=owner, ttl_seconds=ttl_seconds
    )


async def release_lease(
    flags: Any,
    owner: str,
    *,
    flag: str = FLAG_LEASE,
    advisory_key: int = ADVISORY_KEY,
    session: AsyncSession | None = None,
) -> None:
    del flags, advisory_key
    if session is None:
        return
    await release_row_lease(session, name=_lease_name(flag), owner=owner)


async def snapshot_cycle_lease(session: AsyncSession, flag: str = FLAG_LEASE) -> dict[str, Any]:
    return (await snapshot_lease(session, _lease_name(flag))).as_dict()
