"""Strategy A cycle lease + client_order_id allocator (combo #9).

The cycle lock is a DB row lease (``desk_leases.crypto_a``). Session advisory
locks are not used — they do not survive PgBouncer transaction-mode pooling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from services.db_lease import (
    DEFAULT_TTL_SECONDS,
    LEASE_CRYPTO_A,
    acquire_lease,
    heartbeat_lease,
    release_lease,
    replica_id,
    snapshot_lease,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "LEASE_CRYPTO_A",
    "acquire_cycle_lease",
    "allocate_sa9_client_order_id",
    "heartbeat_cycle_lease",
    "idempotency_key",
    "release_cycle_lease",
    "replica_id",
    "snapshot_cycle_lease",
]


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


async def acquire_cycle_lease(
    flags: Any,
    *,
    owner: str,
    now: datetime,
    session: AsyncSession | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[bool, dict[str, Any]]:
    del flags, now
    if session is None:
        return False, {"error": "session_required"}
    snap = await acquire_lease(session, name=LEASE_CRYPTO_A, owner=owner, ttl_seconds=ttl_seconds)
    return snap.acquired, snap.as_dict()


async def heartbeat_cycle_lease(
    session: AsyncSession,
    *,
    owner: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    return await heartbeat_lease(session, name=LEASE_CRYPTO_A, owner=owner, ttl_seconds=ttl_seconds)


async def release_cycle_lease(
    flags: Any,
    owner: str,
    session: AsyncSession | None = None,
) -> None:
    del flags
    if session is None:
        return
    await release_lease(session, name=LEASE_CRYPTO_A, owner=owner)


async def snapshot_cycle_lease(session: AsyncSession) -> dict[str, Any]:
    return (await snapshot_lease(session, LEASE_CRYPTO_A)).as_dict()


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
