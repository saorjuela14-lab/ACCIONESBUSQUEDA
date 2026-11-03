"""Clock-aligned Autopilot ticks + boot recovery. Timing only — not trading logic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from services.autopilot_schedule import (
    AUTOPILOT_CLOCK_OFFSET_SECONDS,
    AUTOPILOT_MISFIRE_GRACE_SECONDS,
    AUTOPILOT_RECOVERY_DELAY_SECONDS,
    make_autopilot_trigger,
    next_aligned_tick,
    parse_last_cycle_at,
    recovery_needed,
    should_run_startup_recovery,
)
from services.scheduler_service import SchedulerService

ET = ZoneInfo("America/New_York")


def test_next_aligned_tick_snaps_to_ten_plus_offset():
    now = datetime(2026, 11, 3, 12, 21, 0, tzinfo=ET)
    nxt = next_aligned_tick(now)
    assert nxt == datetime(2026, 11, 3, 12, 30, 26, tzinfo=ET)


def test_next_aligned_tick_from_just_before_offset():
    now = datetime(2026, 11, 3, 12, 20, 25, tzinfo=ET)
    nxt = next_aligned_tick(now)
    assert nxt == datetime(2026, 11, 3, 12, 20, 26, tzinfo=ET)


def test_next_aligned_tick_on_exact_tick_goes_to_next():
    now = datetime(2026, 11, 3, 12, 20, 26, tzinfo=ET)
    nxt = next_aligned_tick(now)
    assert nxt == datetime(2026, 11, 3, 12, 30, 26, tzinfo=ET)


def test_next_aligned_tick_rolls_over_the_hour():
    now = datetime(2026, 11, 3, 12, 59, 50, tzinfo=ET)
    nxt = next_aligned_tick(now)
    assert nxt == datetime(2026, 11, 3, 13, 0, 26, tzinfo=ET)


def test_cron_trigger_fires_at_clock_aligned_offset():
    trig = make_autopilot_trigger(interval_minutes=10, tz=ET)
    assert isinstance(trig, CronTrigger)
    now = datetime(2026, 11, 3, 12, 21, 0, tzinfo=ET)
    nxt = trig.get_next_fire_time(None, now)
    assert nxt is not None
    assert nxt.minute == 30
    assert nxt.second == AUTOPILOT_CLOCK_OFFSET_SECONDS
    later = trig.get_next_fire_time(nxt, nxt + timedelta(seconds=1))
    assert later is not None
    assert later.minute == 40
    assert later.second == AUTOPILOT_CLOCK_OFFSET_SECONDS


def test_parse_last_cycle_missing_or_unparseable():
    assert parse_last_cycle_at(None) is None
    assert parse_last_cycle_at({}) is None
    assert parse_last_cycle_at({"at": "nope"}) is None


def test_parse_last_cycle_zulu_and_offset():
    z = parse_last_cycle_at({"at": "2026-11-03T17:10:26Z"})
    assert z == datetime(2026, 11, 3, 17, 10, 26, tzinfo=timezone.utc)
    off = parse_last_cycle_at({"at": "2026-11-03T12:10:26-05:00"})
    assert off == datetime(2026, 11, 3, 17, 10, 26, tzinfo=timezone.utc)


def test_recovery_needed_stale_fresh_and_missing():
    now = datetime(2026, 11, 3, 17, 21, 0, tzinfo=timezone.utc)
    assert recovery_needed(None, now) is True
    stale = now - timedelta(minutes=10, seconds=1)
    assert recovery_needed(stale, now) is True
    fresh = now - timedelta(minutes=9, seconds=50)
    assert recovery_needed(fresh, now) is False
    exact = now - timedelta(minutes=10)
    assert recovery_needed(exact, now) is False


def test_startup_recovery_requires_market_session():
    now = datetime(2026, 11, 3, 17, 21, 0, tzinfo=timezone.utc)
    stale = now - timedelta(minutes=26)
    assert should_run_startup_recovery(market_open=False, last_at=stale, now=now) is False
    assert should_run_startup_recovery(market_open=True, last_at=stale, now=now) is True
    assert should_run_startup_recovery(market_open=True, last_at=None, now=now) is True
    fresh = now - timedelta(minutes=2)
    assert should_run_startup_recovery(market_open=True, last_at=fresh, now=now) is False


async def _recovery_svc(monkeypatch, *, last_payload: dict | None, market_open: bool):
    svc = SchedulerService.__new__(SchedulerService)
    svc._settings = MagicMock()
    svc._settings.effective_autopilot_interval_minutes = 10
    ran: list[str] = []

    async def fake_run() -> None:
        ran.append("autopilot")

    svc._run_autopilot = fake_run  # type: ignore[method-assign]
    monkeypatch.setattr("services.scheduler_service.is_market_open", lambda: market_open)

    async def fake_sessions():
        yield object()

    monkeypatch.setattr("services.scheduler_service.get_session", fake_sessions)

    class FakeRepo:
        def __init__(self, session):
            self._session = session

        async def get_json(self, key):
            return last_payload or {}

    monkeypatch.setattr(
        "database.repositories.ops_repository.OpsFlagRepository",
        FakeRepo,
    )
    return svc, ran


@pytest.mark.asyncio
async def test_startup_with_stale_last_cycle_runs_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    stale_at = (now - timedelta(minutes=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    svc, ran = await _recovery_svc(
        monkeypatch,
        last_payload={"at": stale_at, "result": "ok"},
        market_open=True,
    )
    await svc._maybe_run_autopilot_recovery()
    assert ran == ["autopilot"]


@pytest.mark.asyncio
async def test_startup_with_fresh_last_cycle_skips_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh_at = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    svc, ran = await _recovery_svc(
        monkeypatch,
        last_payload={"at": fresh_at, "result": "ok"},
        market_open=True,
    )
    await svc._maybe_run_autopilot_recovery()
    assert ran == []


@pytest.mark.asyncio
async def test_startup_without_last_cycle_runs_recovery(monkeypatch):
    svc, ran = await _recovery_svc(monkeypatch, last_payload={}, market_open=True)
    await svc._maybe_run_autopilot_recovery()
    assert ran == ["autopilot"]


@pytest.mark.asyncio
async def test_startup_outside_market_skips_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    stale_at = (now - timedelta(minutes=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
    svc, ran = await _recovery_svc(
        monkeypatch,
        last_payload={"at": stale_at},
        market_open=False,
    )
    await svc._maybe_run_autopilot_recovery()
    assert ran == []


def test_scheduler_registers_clock_aligned_job_and_recovery(monkeypatch):
    captured: dict[str, dict] = {}
    real_add = AsyncIOScheduler.add_job

    def spy(self, func, trigger=None, **kw):
        job_id = kw.get("id")
        if job_id in {"firm_autopilot", "firm_autopilot_recovery"}:
            captured[job_id] = {"trigger": trigger, "func": func, **kw}
        return real_add(self, func, trigger, **kw)

    monkeypatch.setattr(AsyncIOScheduler, "add_job", spy)
    monkeypatch.setattr(AsyncIOScheduler, "start", lambda self, *a, **k: None)
    svc = SchedulerService()
    svc._settings.autopilot_interval_minutes = 10
    svc.start()

    auto = captured["firm_autopilot"]
    assert isinstance(auto["trigger"], CronTrigger)
    assert auto["misfire_grace_time"] == AUTOPILOT_MISFIRE_GRACE_SECONDS
    assert auto["coalesce"] is True and auto["max_instances"] == 1
    assert "next_run_time" not in auto
    now = datetime(2026, 11, 3, 12, 21, 0, tzinfo=ET)
    nxt = auto["trigger"].get_next_fire_time(None, now)
    assert nxt is not None and nxt.minute == 30 and nxt.second == 26

    rec = captured["firm_autopilot_recovery"]
    assert isinstance(rec["trigger"], DateTrigger)
    delta = (rec["trigger"].run_date - datetime.now(rec["trigger"].run_date.tzinfo)).total_seconds()
    assert 30 <= delta <= 60
    assert rec["func"] == svc._maybe_run_autopilot_recovery


@pytest.mark.asyncio
async def test_recovery_and_scheduled_cycle_share_lease():
    """Recovery goes through AutopilotService.run, so the replica lease blocks a duplicate."""
    from database.models import Base
    from services.autopilot_service import AutopilotService
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def hold(steps, **_kw):
        await asyncio.sleep(0.35)
        steps["ok"] = True
        steps["finished_at"] = datetime.now(timezone.utc).isoformat()
        steps["message"] = "ok"
        return steps

    async with factory() as s1, factory() as s2:
        a = AutopilotService(s1)
        b = AutopilotService(s2)
        a._run_unlocked = hold  # type: ignore[method-assign]
        b._run_unlocked = hold  # type: ignore[method-assign]
        r1, r2 = await asyncio.gather(
            a.run(actor="scheduler_autopilot"),
            b.run(actor="scheduler_autopilot_recovery"),
        )
    await engine.dispose()
    rows = (r1, r2)
    held = [r for r in rows if r.get("aborted") == "replica_lease_held"]
    won = [r for r in rows if not r.get("skipped") and r.get("aborted") != "replica_lease_held"]
    assert len(won) == 1
    assert len(held) == 1


@pytest.mark.asyncio
async def test_recovery_job_invokes_leased_run(monkeypatch):
    """The recovery wrapper calls _run_autopilot, which is the leased AutopilotService path."""
    svc = SchedulerService.__new__(SchedulerService)
    svc._settings = MagicMock()
    svc._settings.effective_autopilot_interval_minutes = 10
    monkeypatch.setattr("services.scheduler_service.is_market_open", lambda: True)
    monkeypatch.setattr(
        "services.scheduler_service.should_run_startup_recovery",
        lambda **_k: True,
    )

    async def fake_sessions():
        yield object()

    monkeypatch.setattr("services.scheduler_service.get_session", fake_sessions)

    class FakeRepo:
        def __init__(self, session):
            pass

        async def get_json(self, key):
            return {}

    monkeypatch.setattr(
        "database.repositories.ops_repository.OpsFlagRepository",
        FakeRepo,
    )
    called = []

    async def fake_run():
        called.append("leased_run")

    svc._run_autopilot = fake_run  # type: ignore[method-assign]
    await svc._maybe_run_autopilot_recovery()
    assert called == ["leased_run"]
    import inspect

    src = inspect.getsource(SchedulerService._run_autopilot)
    assert "AutopilotService" in src and ".run(" in src
