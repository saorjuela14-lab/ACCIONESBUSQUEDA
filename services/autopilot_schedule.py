"""When Autopilot cycles fire — clock-aligned ticks + boot recovery.

Does not change trading logic, LIVE flags, sizing, or entries/exits.
The scheduler uses these helpers so a host restart does not wait a full
interval or leave cycles drifting off :00/:10/:20.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

LAST_CYCLE_FLAG = "firm_autopilot_last_cycle"

# Historic cadence observed in prod before restarts shifted it (:x0:26).
AUTOPILOT_CLOCK_OFFSET_SECONDS = 26
AUTOPILOT_DEFAULT_INTERVAL_MINUTES = 10
# After boot, wait for cold-start (DB, broker) before a catch-up cycle.
AUTOPILOT_RECOVERY_DELAY_SECONDS = 45
AUTOPILOT_MISFIRE_GRACE_SECONDS = 300


def parse_last_cycle_at(payload: dict[str, Any] | None) -> datetime | None:
    """Parse ``firm_autopilot_last_cycle.at`` (or finished/started) as UTC."""
    if not payload:
        return None
    raw = payload.get("at") or payload.get("finished_at") or payload.get("started_at")
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def recovery_needed(
    last_at: datetime | None,
    now: datetime,
    *,
    max_age_minutes: int = AUTOPILOT_DEFAULT_INTERVAL_MINUTES,
) -> bool:
    """True when no last cycle exists or it is older than the Autopilot interval."""
    if last_at is None:
        return True
    if max_age_minutes <= 0:
        return True
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_at) > timedelta(minutes=max_age_minutes)


def should_run_startup_recovery(
    *,
    market_open: bool,
    last_at: datetime | None,
    now: datetime,
    max_age_minutes: int = AUTOPILOT_DEFAULT_INTERVAL_MINUTES,
) -> bool:
    """Boot catch-up: only during the regular session, and only if the last cycle is stale."""
    if not market_open:
        return False
    return recovery_needed(last_at, now, max_age_minutes=max_age_minutes)


def next_aligned_tick(
    now: datetime,
    *,
    interval_minutes: int = AUTOPILOT_DEFAULT_INTERVAL_MINUTES,
    offset_seconds: int = AUTOPILOT_CLOCK_OFFSET_SECONDS,
) -> datetime:
    """Next wall-clock fire at minutes multiple of ``interval`` plus a fixed second offset.

    Example with interval=10, offset=26: 12:00:26, 12:10:26, 12:20:26, ...
    If ``now`` is exactly on a tick, returns the following tick.
    """
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    slot = (now.minute // interval_minutes) * interval_minutes
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(
        minutes=slot, seconds=offset_seconds
    )
    if candidate <= now:
        candidate += timedelta(minutes=interval_minutes)
    return candidate


def make_autopilot_trigger(
    *,
    interval_minutes: int,
    offset_seconds: int = AUTOPILOT_CLOCK_OFFSET_SECONDS,
    tz: ZoneInfo | timezone,
    now: datetime | None = None,
):
    """Clock-aligned trigger. Cron when the interval divides an hour; else interval from next tick."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")
    if 60 % interval_minutes == 0:
        return CronTrigger(
            minute=f"*/{interval_minutes}",
            second=offset_seconds,
            timezone=tz,
        )
    now = now or datetime.now(tz)
    start = next_aligned_tick(
        now, interval_minutes=interval_minutes, offset_seconds=offset_seconds
    )
    return IntervalTrigger(minutes=interval_minutes, start_date=start, timezone=tz)
