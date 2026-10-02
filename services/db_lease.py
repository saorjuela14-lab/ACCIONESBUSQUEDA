"""Row-based cycle lease — works with Neon PgBouncer transaction pooling.

Session advisory locks (pg_advisory_lock) die when the connection returns to the
pool after each transaction. This module uses a single UPDATE ... RETURNING on
``desk_leases`` and always computes ``expires_at`` with the database clock
(``now()`` / ``datetime('now')``), never the replica wall clock.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeskLeaseORM

log = logging.getLogger("nexbuy.db_lease")

LEASE_CRYPTO_A = "crypto_a"
LEASE_LIVE_STOCKS = "live_stocks"
LEASE_LIVE_ENTRY = "live_entry"
DEFAULT_TTL_SECONDS = 120
ALERT_AFTER_MISSES = 2


@dataclass
class LeaseSnapshot:
    name: str
    acquired: bool
    owner: str | None
    expires_at: str | None
    acquired_at: str | None
    heartbeat_at: str | None
    misses_consecutive: int
    alert: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "acquired": self.acquired,
            "lease_owner": self.owner,
            "lease_expires_at": self.expires_at,
            "lease_acquired_at": self.acquired_at,
            "lease_heartbeat_at": self.heartbeat_at,
            "lease_misses_consecutive": self.misses_consecutive,
            "alert": self.alert,
        }


def replica_id() -> str:
    return (os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or socket.gethostname() or "replica")[:80]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text_v = str(value).strip()
    return text_v or None


def _dialect(session: AsyncSession) -> str:
    bind = session.get_bind()
    name = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    return name


def _now_sql(dialect: str) -> str:
    return "now()" if dialect.startswith("postgres") else "datetime('now')"


def _expires_sql(dialect: str, ttl_seconds: int) -> str:
    ttl = max(1, int(ttl_seconds))
    if dialect.startswith("postgres"):
        return f"now() + make_interval(secs => {ttl})"
    return f"datetime('now', '+{ttl} seconds')"


def _expired_sql(dialect: str) -> str:
    now = _now_sql(dialect)
    return f"expires_at < {now}"


async def _ensure_row(session: AsyncSession, name: str) -> None:
    dialect = _dialect(session)
    past = "now() - interval '1 day'" if dialect.startswith("postgres") else "datetime('now', '-1 day')"
    if dialect.startswith("postgres"):
        await session.execute(
            text(
                "INSERT INTO desk_leases (name, owner, expires_at, acquired_at, heartbeat_at, misses_consecutive) "
                f"VALUES (:n, NULL, {past}, NULL, NULL, 0) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": name},
        )
    else:
        await session.execute(
            text(
                "INSERT OR IGNORE INTO desk_leases (name, owner, expires_at, acquired_at, heartbeat_at, misses_consecutive) "
                f"VALUES (:n, NULL, {past}, NULL, NULL, 0)"
            ),
            {"n": name},
        )


def _row_to_snap(name: str, row: Any, *, acquired: bool, alert: bool = False) -> LeaseSnapshot:
    mapping = row._mapping if hasattr(row, "_mapping") else None
    if mapping is not None:
        owner = mapping.get("owner")
        expires = mapping.get("expires_at")
        acquired_at = mapping.get("acquired_at")
        heartbeat = mapping.get("heartbeat_at")
        misses = mapping.get("misses_consecutive")
    else:
        owner = row[0] if len(row) > 0 else None
        expires = row[1] if len(row) > 1 else None
        acquired_at = row[2] if len(row) > 2 else None
        heartbeat = row[3] if len(row) > 3 else None
        misses = row[4] if len(row) > 4 else 0
    try:
        misses_i = int(misses or 0)
    except (TypeError, ValueError):
        misses_i = 0
    return LeaseSnapshot(
        name=name,
        acquired=acquired,
        owner=str(owner) if owner else None,
        expires_at=_iso(expires),
        acquired_at=_iso(acquired_at),
        heartbeat_at=_iso(heartbeat),
        misses_consecutive=misses_i,
        alert=alert,
    )


async def snapshot_lease(session: AsyncSession, name: str) -> LeaseSnapshot:
    await _ensure_row(session, name)
    row = (
        await session.execute(
            text(
                "SELECT owner, expires_at, acquired_at, heartbeat_at, misses_consecutive "
                "FROM desk_leases WHERE name = :n"
            ),
            {"n": name},
        )
    ).first()
    if not row:
        return LeaseSnapshot(name=name, acquired=False, owner=None, expires_at=None, acquired_at=None, heartbeat_at=None, misses_consecutive=0)
    return _row_to_snap(name, row, acquired=False)


async def snapshot_leases(session: AsyncSession, names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    names = names or [LEASE_CRYPTO_A, LEASE_LIVE_STOCKS, LEASE_LIVE_ENTRY]
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        try:
            out[name] = (await snapshot_lease(session, name)).as_dict()
        except Exception as exc:
            out[name] = {"name": name, "error": str(exc), "acquired": False}
    return out


async def acquire_lease(
    session: AsyncSession,
    *,
    name: str,
    owner: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> LeaseSnapshot:
    """Atomically take or renew the named lease. Misses increment when we lose."""
    await _ensure_row(session, name)
    dialect = _dialect(session)
    expires = _expires_sql(dialect, ttl_seconds)
    now = _now_sql(dialect)
    expired = _expired_sql(dialect)
    taken = (
        await session.execute(
            text(
                "UPDATE desk_leases SET owner = :me, "
                f"expires_at = {expires}, "
                f"acquired_at = CASE WHEN owner IS NULL OR owner <> :me THEN {now} ELSE acquired_at END, "
                f"heartbeat_at = {now}, "
                "misses_consecutive = 0 "
                f"WHERE name = :n AND ({expired} OR owner = :me) "
                "RETURNING owner, expires_at, acquired_at, heartbeat_at, misses_consecutive"
            ),
            {"me": owner, "n": name},
        )
    ).first()
    if taken:
        await session.commit()
        return _row_to_snap(name, taken, acquired=True)
    bumped = (
        await session.execute(
            text(
                "UPDATE desk_leases SET misses_consecutive = misses_consecutive + 1 "
                "WHERE name = :n "
                "RETURNING owner, expires_at, acquired_at, heartbeat_at, misses_consecutive"
            ),
            {"n": name},
        )
    ).first()
    await session.commit()
    snap = _row_to_snap(name, bumped, acquired=False) if bumped else LeaseSnapshot(
        name=name, acquired=False, owner=None, expires_at=None, acquired_at=None, heartbeat_at=None, misses_consecutive=1
    )
    if snap.misses_consecutive >= ALERT_AFTER_MISSES:
        snap.alert = True
        log.warning(
            "lease_miss_alert name=%s misses=%s owner=%s expires_at=%s replica=%s",
            name,
            snap.misses_consecutive,
            snap.owner,
            snap.expires_at,
            owner,
        )
    return snap


async def heartbeat_lease(
    session: AsyncSession,
    *,
    name: str,
    owner: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    dialect = _dialect(session)
    expires = _expires_sql(dialect, ttl_seconds)
    now = _now_sql(dialect)
    row = (
        await session.execute(
            text(
                "UPDATE desk_leases SET "
                f"expires_at = {expires}, heartbeat_at = {now} "
                "WHERE name = :n AND owner = :me "
                "RETURNING owner"
            ),
            {"n": name, "me": owner},
        )
    ).first()
    await session.commit()
    return bool(row)


async def release_lease(session: AsyncSession, *, name: str, owner: str) -> bool:
    """Release only if we still own the row."""
    dialect = _dialect(session)
    past = "now() - interval '1 day'" if dialect.startswith("postgres") else "datetime('now', '-1 day')"
    row = (
        await session.execute(
            text(
                "UPDATE desk_leases SET owner = NULL, "
                f"expires_at = {past}, heartbeat_at = NULL "
                "WHERE name = :n AND owner = :me "
                "RETURNING name"
            ),
            {"n": name, "me": owner},
        )
    ).first()
    await session.commit()
    return bool(row)


async def force_expire_for_tests(session: AsyncSession, name: str) -> None:
    """Test helper: expire the row using the database clock."""
    dialect = _dialect(session)
    past = "now() - interval '1 day'" if dialect.startswith("postgres") else "datetime('now', '-1 day')"
    await session.execute(
        text(f"UPDATE desk_leases SET expires_at = {past} WHERE name = :n"),
        {"n": name},
    )
    await session.commit()


# Keep the ORM imported so metadata.create_all sees the table even if unused here.
_ = DeskLeaseORM
