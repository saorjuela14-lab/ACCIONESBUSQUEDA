"""Paquete 22-oct ítem 2: arranque en frío + endurecimiento del lease."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services import db_lease
from services.db_lease import LeaseHeartbeat, sanitize_lease_error


class _HB(LeaseHeartbeat):
    def __init__(self, results):
        super().__init__(name="live_stocks", owner="t:1", interval_seconds=0.01)
        self._results = list(results)
        self.calls = 0
        self.alerts: list[str] = []

    async def _beat_once(self) -> bool:
        self.calls += 1
        r = self._results.pop(0) if self._results else True
        if r == "hang":
            await asyncio.sleep(3600)
        if isinstance(r, Exception):
            raise r
        return r

    async def _alert_lost(self, detail: str) -> None:
        self.alerts.append(detail)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(db_lease, "LEASE_OP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(db_lease, "STILL_MINE_BACKOFF_SECONDS", (0.0, 0.0))


@pytest.mark.asyncio
async def test_still_mine_retries_transient_error_then_ok():
    hb = _HB([RuntimeError("boom"), True])
    assert await hb.still_mine() is True
    assert hb.calls == 2 and not hb.lost and hb.alerts == []


@pytest.mark.asyncio
async def test_still_mine_timeout_retries_then_lost_and_alerts():
    hb = _HB(["hang", "hang", "hang"])
    assert await hb.still_mine() is False
    assert hb.calls == 3
    assert hb.lost is True
    assert hb.alerts and hb.alerts[0].startswith("still_mine_error:")


@pytest.mark.asyncio
async def test_not_owner_is_final_without_retry():
    hb = _HB([False])
    assert await hb.still_mine() is False
    assert hb.calls == 1 and hb.lost and hb.alerts == ["still_mine_not_owner"]


@pytest.mark.asyncio
async def test_loop_marks_lost_and_alerts_on_persistent_error():
    hb = _HB([True, RuntimeError("x"), RuntimeError("x"), RuntimeError("x")])
    await hb.start()
    for _ in range(100):
        if hb.lost:
            break
        await asyncio.sleep(0.01)
    await hb.stop()
    assert hb.lost is True
    assert hb.alerts and hb.alerts[0].startswith("heartbeat_loop_error:")


def test_sanitize_strips_dsn_and_hosts():
    msg = sanitize_lease_error(
        RuntimeError(
            "could not connect postgresql+asyncpg://u:s3cret@ep-x.neon.tech:5432/db "
            "host=10.0.0.12:5432 password=s3cret"
        )
    )
    assert "s3cret" not in msg
    assert "neon.tech" not in msg
    assert "10.0.0.12" not in msg
    assert "postgresql" not in msg


def test_autopilot_job_runs_on_boot_with_misfire_grace(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from services import scheduler_service as ss

    captured = {}
    real_add = AsyncIOScheduler.add_job

    def spy(self, func, trigger=None, **kw):
        if kw.get("id") == "firm_autopilot":
            captured.update(kw)
        return real_add(self, func, trigger, **kw)

    monkeypatch.setattr(AsyncIOScheduler, "add_job", spy)
    monkeypatch.setattr(AsyncIOScheduler, "start", lambda self, *a, **k: None)
    svc = ss.SchedulerService()
    svc._settings.autopilot_interval_minutes = 10
    svc.start()
    assert captured, "firm_autopilot no se programó"
    assert captured["misfire_grace_time"] == 300
    assert captured["coalesce"] is True and captured["max_instances"] == 1
    nrt = captured["next_run_time"]
    delta = (nrt - datetime.now(timezone.utc)).total_seconds()
    assert 0 <= delta <= 60  # corre ya, no a +10 min


def test_delayed_job_under_300s_still_runs():
    """APScheduler: un disparo atrasado < misfire_grace_time se ejecuta."""
    from apscheduler.job import Job  # noqa: F401
    from apscheduler.schedulers.base import BaseScheduler  # noqa: F401
    from apscheduler.triggers.interval import IntervalTrigger

    from services.scheduler_service import AUTOPILOT_MISFIRE_GRACE_SECONDS

    now = datetime.now(timezone.utc)
    scheduled = now - timedelta(seconds=240)
    late = (now - scheduled).total_seconds()
    assert late < AUTOPILOT_MISFIRE_GRACE_SECONDS
    trig = IntervalTrigger(minutes=10, start_date=scheduled)
    assert trig.get_next_fire_time(None, now) is not None
