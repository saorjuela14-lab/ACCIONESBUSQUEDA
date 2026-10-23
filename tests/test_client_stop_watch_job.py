"""In-app client stop_watch job: flag gating, DB lease, DB state, staleness."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from database.repositories.ops_repository import OpsFlagRepository
from services.db_lease import acquire_lease
from services.protection import stop_watch_job as job
from services.protection.stop_watch import ProtectedPosition, StopWatchService
from tests.test_client_stop_watch import FakeBroker


def _settings(enabled=True):
    return SimpleNamespace(
        client_stop_watch_enabled=enabled,
        client_stop_watch_job_interval_s=20,
        client_stop_watch_heartbeat_max_s=90,
        client_stop_watch_stale_alert_s=120,
        client_stop_watch_broker_timeout_s=20.0,
        client_stop_watch_quote_timeout_s=15.0,
        effective_alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        alpaca_data_feed="iex",
    )


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    mk = async_sessionmaker(engine, expire_on_commit=False)

    async def sessions():
        async with mk() as s:
            yield s

    yield sessions
    await engine.dispose()


def _svc_factory(broker, prices):
    async def q(sym):
        return prices.get(sym, 0)

    return lambda: StopWatchService(broker, q, file_backend=False, enabled_override=True, paper=True)


def _scheduler(enabled):
    from services import scheduler_service as ss

    svc = ss.SchedulerService.__new__(ss.SchedulerService)
    svc._settings = _settings(enabled)
    added = []
    svc._scheduler = SimpleNamespace(add_job=lambda fn, trig, **kw: added.append(kw))
    svc._register_client_stop_watch()
    return added


def test_job_registered_only_when_flag_on():
    assert _scheduler(False) == []
    assert _scheduler(None) == []
    added = _scheduler(True)
    ids = {a["id"] for a in added}
    assert ids == {"client_stop_watch", "client_stop_watch_staleness"}
    main = next(a for a in added if a["id"] == "client_stop_watch")
    assert main["max_instances"] == 1 and main["coalesce"] is True and main["misfire_grace_time"]


def test_default_service_factory_uses_firm_broker_when_settings_are_live(monkeypatch):
    from providers.broker import factory as broker_factory

    monkeypatch.delenv("CLIENT_STOP_WATCH_LIVE_ACK", raising=False)
    broker = FakeBroker(paper=False)
    broker.base_url = "https://api.alpaca.markets"
    monkeypatch.setattr(broker_factory, "get_broker_provider", lambda: broker)
    settings = _settings(True)
    settings.effective_alpaca_paper = False

    svc = job._default_service_factory(settings)

    assert svc._b is broker
    assert svc.paper is False
    assert svc.enabled() is False  # LIVE still requires CLIENT_STOP_WATCH_LIVE_ACK


def test_settings_default_off():
    from config.settings import Settings

    assert Settings.model_fields["client_stop_watch_enabled"].default is False


@pytest.mark.asyncio
async def test_disabled_tick_does_nothing(factory):
    rep = await job.run_stop_watch_tick(session_factory=factory, settings=_settings(False))
    assert rep == {"skipped": "disabled"}


@pytest.mark.asyncio
async def test_tick_skips_when_lease_held_by_other_replica(factory):
    async for s in factory():
        await acquire_lease(s, name=job.LEASE_CLIENT_STOP_WATCH, owner="other", ttl_seconds=120)
    called = []
    rep = await job.run_stop_watch_tick(
        session_factory=factory,
        service_factory=lambda: called.append(1),
        settings=_settings(True),
    )
    assert rep["skipped"] == "lease_held" and not called


@pytest.mark.asyncio
async def test_state_round_trips_through_db(factory, monkeypatch):
    monkeypatch.setattr(job, "_reconciled_in_process", False)
    b = FakeBroker()
    b.positions["BTCUSD"] = {"symbol": "BTCUSD", "qty": "0.5", "qty_available": "0.5",
                             "avg_entry_price": "100", "asset_class": "crypto"}
    rep = await job.run_stop_watch_tick(
        session_factory=factory, service_factory=_svc_factory(b, {"BTC/USD": 101}), settings=_settings(True)
    )
    assert rep.get("ok") is True, rep
    async for s in factory():
        state = await OpsFlagRepository(s).get_json(job.STATE_KEY)
        hb = await OpsFlagRepository(s).get_json(job.HEARTBEAT_KEY)
    assert "BTC/USD" in state and state["BTC/USD"]["stop"] > 0
    assert time.time() - hb["ts"] < 5
    # second tick loads state from DB (no re-adoption)
    n_orders = len(b.orders)
    svc2 = _svc_factory(b, {"BTC/USD": 101})()
    svc2.load_state(state)
    assert isinstance(svc2.positions["BTC/USD"], ProtectedPosition)
    rep2 = await job.run_stop_watch_tick(
        session_factory=factory, service_factory=_svc_factory(b, {"BTC/USD": 101}), settings=_settings(True)
    )
    assert not any(a["action"] == "adopted" for a in rep2["actions"])
    assert len(b.orders) == n_orders  # idempotent: no duplicate backup stop
    # lease released after tick
    async for s in factory():
        got = await acquire_lease(s, name=job.LEASE_CLIENT_STOP_WATCH, owner="x")
    assert got.acquired


@pytest.mark.asyncio
async def test_staleness_reported_and_alerted(factory, monkeypatch):
    sent = []

    async def fake_alert(kind, **kw):
        sent.append(kind)
        return True

    import services.desk_ops_alert as doa

    monkeypatch.setattr(doa, "emit_desk_ops_alert", fake_alert)
    async for s in factory():
        st = await job.stop_watch_status(s, _settings(True))
        assert st["stale"] is True and st["last_tick_ts"] is None
        await OpsFlagRepository(s).set_json(job.HEARTBEAT_KEY, {"ts": time.time() - 300})
        st = await job.stop_watch_status(s, _settings(True))
        assert st["stale"] is True and st["age_s"] >= 300
        assert (await job.stop_watch_status(s, _settings(False)))["stale"] is False
        await OpsFlagRepository(s).set_json(job.HEARTBEAT_KEY, {"ts": time.time() - 10})
        assert (await job.stop_watch_status(s, _settings(True)))["stale"] is False
        await OpsFlagRepository(s).set_json(job.HEARTBEAT_KEY, {"ts": time.time() - 300})
    await job.stop_watch_staleness_check(session_factory=factory, settings=_settings(True))
    assert sent == ["stop_watch_heartbeat_stale"]
