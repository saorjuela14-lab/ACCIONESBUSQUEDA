"""PR #137 risk follow-up: live stop IDs, API reservation hole, lease owner/heartbeat, alerts."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from domain.broker import BrokerOrderRequest, BrokerOrderResult, ExecuteLine, ExecuteOrdersRequest
from services.db_lease import (
    LEASE_LIVE_STOCKS,
    LeaseHeartbeat,
    acquire_lease,
    replica_id,
    run_owner,
)
from services.desk_ops_alert import (
    KIND_LEASE_MISSES,
    KIND_ORDER_UNCERTAIN,
    KIND_STOP_NOT_LIVE,
    KIND_STOP_RECHECK_EMPTY,
    emit_desk_ops_alert,
    reset_desk_ops_alert_dedupe,
)
from services.order_idempotency import ALPACA_DUPLICATE_COID_CODE, order_is_live_stop


def _http_err(status: int, *, code: int | None = None, message: str = "nope") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.alpaca.markets/v2/orders")
    resp = httpx.Response(
        status,
        json={"code": code, "message": message} if code is not None else {"message": message},
        request=req,
    )
    err = httpx.HTTPStatusError(f"Alpaca {status}: {message}", request=req, response=resp)
    err.alpaca_code = code
    err.alpaca_status = status
    return err


def test_api_models_ignore_reservation_bypass():
    req = BrokerOrderRequest.model_validate(
        {
            "symbol": "SNAP",
            "qty": 1,
            "side": "buy",
            "entry_slot_reserved": True,
            "skip_daily_cap": True,
        }
    )
    dumped = req.model_dump()
    assert "entry_slot_reserved" not in dumped
    assert "skip_daily_cap" not in dumped
    exe = ExecuteOrdersRequest.model_validate(
        {
            "lines": [{"ticker": "SNAP", "shares": 1}],
            "entry_slot_reserved": True,
            "skip_daily_cap": True,
        }
    )
    assert "entry_slot_reserved" not in exe.model_dump()
    assert "skip_daily_cap" not in exe.model_dump()


def test_replica_id_includes_pid_and_run_owner_is_unique():
    rid = replica_id()
    assert ":" in rid
    assert str(os.getpid()) in rid
    a = run_owner()
    b = run_owner()
    assert a != b
    assert a.startswith(rid + ":")
    assert b.startswith(rid + ":")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["filled", "replaced", "canceled"])
async def test_standalone_stop_stale_status_retries_then_live(status):
    from services.alpaca_order_service import AlpacaOrderService

    live = {
        "id": "stop-2",
        "symbol": "SNAP",
        "qty": "1",
        "side": "sell",
        "type": "stop",
        "status": "held",
        "stop_price": "5.4",
        "client_order_id": "live-SNAP-20261002-stop-2",
    }
    stale = {
        "id": "stop-1",
        "symbol": "SNAP",
        "qty": "1",
        "side": "sell",
        "type": "stop",
        "status": status,
        "stop_price": "5.4",
        "client_order_id": "live-SNAP-20261002-stop-1",
    }
    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = None
    inner.submit_order = AsyncMock(
        side_effect=[
            _http_err(422, code=ALPACA_DUPLICATE_COID_CODE, message="client_order_id must be unique"),
            live,
        ]
    )
    inner.get_order_by_client_order_id = AsyncMock(return_value=stale)
    inner.get_positions = AsyncMock(return_value=[{"symbol": "SNAP", "qty": "1"}])
    svc = AlpacaOrderService(broker=inner)
    svc.get_positions = AsyncMock(return_value=[SimpleNamespace(symbol="SNAP", qty=1)])
    ids = iter(["live-SNAP-20261002-stop-1", "live-SNAP-20261002-stop-2"])

    async def _alloc(_sym: str) -> str:
        return next(ids)

    with (
        patch.object(svc, "find_working_stop", AsyncMock(return_value=None)),
        patch.object(svc, "_allocate_stop_client_order_id", _alloc),
        patch("utils.market_hours.eod_may_submit_orders", return_value=True),
        patch("services.live_safety.production_trading_unconfigured", return_value=False),
        patch("services.desk_ops_alert.emit_desk_ops_alert", AsyncMock()),
    ):
        out = await svc.replace_protective_stop(symbol="SNAP", qty=1, stop_price=5.40)
    assert out is not None
    assert out.error is None
    assert out.status == "held"
    assert order_is_live_stop(out) is True
    assert inner.submit_order.await_count == 2
    first_cid = inner.submit_order.await_args_list[0].args[0]["client_order_id"]
    second_cid = inner.submit_order.await_args_list[1].args[0]["client_order_id"]
    assert first_cid != second_cid
    assert first_cid.endswith("-1")
    assert second_cid.endswith("-2")


@pytest.mark.asyncio
async def test_sync_broker_stop_never_reports_gtc_without_live_stop():
    from domain.ops import PositionMandate
    from services.position_lifecycle_service import PositionLifecycleService

    life = PositionLifecycleService(MagicMock(), MagicMock())
    life._settings = SimpleNamespace(lifecycle_sync_broker_stops=True)
    life._broker.is_configured.return_value = True
    life._broker.replace_protective_stop = AsyncMock(
        return_value=BrokerOrderResult(
            id="old",
            symbol="SNAP",
            qty=1,
            side="sell",
            type="stop",
            status="filled",
            error=None,
        )
    )
    mandate = PositionMandate(symbol="SNAP", qty=1, entry_price=6, stop_loss=5.48)
    detail = await life._sync_broker_stop(mandate, 5.48)
    assert "broker GTC stop" not in (detail or "")
    assert "not live" in (detail or "")


@pytest.mark.asyncio
async def test_submit_one_ignores_json_reservation_and_hits_daily_cap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database.models import Base
    from database.repositories.ops_repository import OpsFlagRepository
    from services.alpaca_order_service import AlpacaOrderService
    from services.live_safety import FLAG_ENTRY_DAY, et_today

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        flags = OpsFlagRepository(session)
        await flags.set_json(
            FLAG_ENTRY_DAY, {"et_date": et_today(), "count": 1, "symbols": ["SNAP"]}
        )

        async def _sessions():
            yield session

        inner = MagicMock()
        inner.paper = False
        inner.last_request_id = "rid"
        inner.submit_order = AsyncMock(side_effect=AssertionError("must not skip cap"))
        svc = AlpacaOrderService(broker=inner)
        svc.get_account = AsyncMock(return_value=SimpleNamespace(equity=22.0))
        parsed = BrokerOrderRequest.model_validate(
            {
                "symbol": "SNAP",
                "qty": 1,
                "side": "buy",
                "client_order_id": "desk-api-1",
                "entry_slot_reserved": True,
                "skip_daily_cap": True,
            }
        )
        with (
            patch("database.engine.get_session", new=_sessions),
            patch("services.kill_switch_service.KillSwitchService") as KS,
            patch(
                "services.deposited_capital_service.resolve_trading_base",
                AsyncMock(return_value=SimpleNamespace(amount=21.76)),
            ),
            patch("services.live_safety.production_trading_unconfigured", return_value=False),
            patch("utils.market_hours.eod_may_submit_orders", return_value=True),
            patch("services.live_safety.live_entry_blocked", return_value=(False, "ok")),
        ):
            KS.return_value.is_active = AsyncMock(return_value=False)
            out = await svc.submit_one(parsed)
    await engine.dispose()
    assert out.error == "max_1_entry_per_day"
    inner.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_same_replica_scheduler_and_api_cannot_overlap():
    from database.models import Base
    from services.autopilot_service import AutopilotService

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
            b.run(actor="user_autopilot"),
        )
    await engine.dispose()
    rows = (r1, r2)
    held = [r for r in rows if r.get("aborted") == "replica_lease_held"]
    won = [r for r in rows if not r.get("skipped") and r.get("aborted") != "replica_lease_held"]
    assert len(won) == 1
    assert len(held) == 1


@pytest.mark.asyncio
async def test_heartbeat_holds_lease_longer_than_ttl():
    from database.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = "host:1:abc12345"

    async def sess_factory():
        async with factory() as s:
            yield s

    async with factory() as session:
        got = await acquire_lease(
            session, name=LEASE_LIVE_STOCKS, owner=owner, ttl_seconds=2
        )
        assert got.acquired is True
        hb = LeaseHeartbeat(
            name=LEASE_LIVE_STOCKS,
            owner=owner,
            ttl_seconds=2,
            interval_seconds=0.4,
            session_factory=sess_factory,
        )
        await hb.start()
        await asyncio.sleep(2.6)
        assert await hb.still_mine() is True
        other = await acquire_lease(
            session, name=LEASE_LIVE_STOCKS, owner="other:2:zzzzzzzz", ttl_seconds=2
        )
        assert other.acquired is False
        await hb.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_lease_lost_skips_entries_keeps_exits():
    from services.autopilot_service import AutopilotService

    session = MagicMock()
    svc = AutopilotService(session)
    svc._broker = MagicMock()
    svc._broker.is_configured.return_value = False
    svc._broker.paper = False
    svc._audit.record = AsyncMock()
    life = MagicMock()
    life.scan = AsyncMock(
        return_value=SimpleNamespace(positions=1, exits=["SNAP"], actions=[], warnings=[])
    )
    recon = MagicMock()
    recon.reconcile = AsyncMock(
        return_value=SimpleNamespace(diffs=[], synced=False, portfolio_id=None, message="ok")
    )
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={})
    flags.set_json = AsyncMock()

    class Lost:
        async def still_mine(self) -> bool:
            return False

    with (
        patch("services.autopilot_service.KillSwitchService") as KS,
        patch("services.autopilot_service.ReconcileService", return_value=recon),
        patch("services.autopilot_service.PositionLifecycleService", return_value=life),
        patch("services.autopilot_service.OpsFlagRepository", return_value=flags),
        patch("services.db_lease.acquire_lease", AsyncMock(return_value=SimpleNamespace(acquired=True, owner="me"))),
        patch("services.db_lease.release_lease", AsyncMock()),
        patch("services.live_safety.arm_deposited_brake_if_needed", AsyncMock(return_value=None)),
        patch(
            "services.deposited_capital_service.get_deposited_base",
            AsyncMock(
                return_value=SimpleNamespace(
                    amount=21.76,
                    buy_allowed=True,
                    source="env:DEPOSITED_BASE_USD",
                    warnings=(),
                )
            ),
        ),
        patch("services.intraday_flat_service.IntradayFlatService") as Flat,
        patch("services.risk_policy_service.RiskPolicyService") as Risk,
        patch("services.holdings_strategy_review_service.HoldingsStrategyReviewService") as Hold,
    ):
        KS.return_value.is_active = AsyncMock(return_value=False)
        Flat.return_value.run = AsyncMock(return_value={"skipped": True})
        Risk.return_value.status = AsyncMock(
            return_value=SimpleNamespace(
                macro=SimpleNamespace(mode="neutral", trading_allowed=True, thesis="", size_multiplier=1)
            )
        )
        Hold.return_value.review = AsyncMock(return_value={})
        out = await svc._run_unlocked(
            {"started_at": "t", "actor": "test"},
            session_label="t",
            execute_trades=True,
            actor="test",
            lease_guard=Lost(),
        )
    assert out["auto_execute"]["reason"] == "lease_lost_mid_cycle"
    assert out["recommendations"]["reason"] == "lease_lost_mid_cycle"
    life.scan.assert_awaited()


@pytest.mark.asyncio
async def test_desk_ops_alerts_dedupe_and_kinds():
    reset_desk_ops_alert_dedupe()
    push = MagicMock()
    push.notify_message = AsyncMock(return_value={"telegram": True})
    with patch("services.push_notification_service.PushNotificationService", return_value=push):
        ok1 = await emit_desk_ops_alert(KIND_LEASE_MISSES, owner="host:1:aaa", detail="misses=2")
        ok2 = await emit_desk_ops_alert(KIND_LEASE_MISSES, owner="host:1:aaa", detail="misses=2")
        ok3 = await emit_desk_ops_alert(KIND_ORDER_UNCERTAIN, detail="SNAP cid=x")
        ok4 = await emit_desk_ops_alert(KIND_STOP_RECHECK_EMPTY, detail="SNAP empty")
        ok5 = await emit_desk_ops_alert(KIND_STOP_NOT_LIVE, detail="SNAP filled")
    assert ok1 is True
    assert ok2 is False
    assert ok3 is True
    assert ok4 is True
    assert ok5 is True
    assert push.notify_message.await_count == 4


@pytest.mark.asyncio
async def test_uncertain_order_alerts():
    from services.alpaca_order_service import AlpacaOrderService

    reset_desk_ops_alert_dedupe()
    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = "rid"
    inner.submit_order = AsyncMock(side_effect=httpx.ReadTimeout("no reply"))
    inner.get_order_by_client_order_id = AsyncMock(
        side_effect=_http_err(503, message="unavailable")
    )
    svc = AlpacaOrderService(broker=inner)
    with (
        patch("services.live_safety.production_trading_unconfigured", return_value=False),
        patch("utils.market_hours.eod_may_submit_orders", return_value=True),
        patch("services.desk_ops_alert.emit_desk_ops_alert", AsyncMock()) as alert,
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(
                symbol="SNAP", qty=1, side="buy", client_order_id="live-SNAP-20261002-buy-1"
            )
        )
    assert out.error == "order_uncertain_no_retry"
    alert.assert_awaited()
    assert alert.await_args.args[0] == KIND_ORDER_UNCERTAIN


@pytest.mark.asyncio
async def test_empty_stop_recheck_alerts():
    from services.alpaca_order_service import AlpacaOrderService

    reset_desk_ops_alert_dedupe()
    inner = MagicMock()
    inner.paper = True
    inner.last_request_id = None
    inner.submit_order = AsyncMock(side_effect=RuntimeError("insufficient qty available"))
    inner.get_order_by_client_order_id = AsyncMock(return_value=None)
    svc = AlpacaOrderService(broker=inner)
    svc.get_positions = AsyncMock(return_value=[SimpleNamespace(symbol="SNAP", qty=1)])
    with (
        patch.object(svc, "find_working_stop", AsyncMock(return_value=None)),
        patch.object(svc, "_allocate_stop_client_order_id", AsyncMock(return_value="stop-uuid-1")),
        patch("utils.market_hours.eod_may_submit_orders", return_value=True),
        patch("services.live_safety.production_trading_unconfigured", return_value=False),
        patch("services.desk_ops_alert.emit_desk_ops_alert", AsyncMock()) as alert,
    ):
        out = await svc.replace_protective_stop(symbol="SNAP", qty=1, stop_price=5.60)
    assert out.error == "insufficient_qty_stop_recheck_empty"
    assert any(c.args[0] == KIND_STOP_RECHECK_EMPTY for c in alert.await_args_list)


@pytest.mark.asyncio
async def test_e2e_nested_bracket_held_stop_via_provider():
    from providers.broker.alpaca_provider import AlpacaBrokerProvider
    from services.alpaca_order_service import AlpacaOrderService

    nested = [
        {
            "id": "parent-1",
            "symbol": "SNAP",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "status": "filled",
            "filled_qty": "1",
            "legs": [
                {
                    "id": "tp-1",
                    "symbol": "SNAP",
                    "qty": "1",
                    "side": "sell",
                    "type": "limit",
                    "status": "new",
                    "limit_price": "7.12",
                },
                {
                    "id": "sl-1",
                    "symbol": "SNAP",
                    "qty": "1",
                    "side": "sell",
                    "type": "stop",
                    "status": "held",
                    "stop_price": "5.48",
                },
            ],
        }
    ]
    provider = AlpacaBrokerProvider(api_key="k", secret_key="s", paper=True)
    provider._request = AsyncMock(return_value=nested)
    svc = AlpacaOrderService(broker=provider)
    found = await svc.find_working_stop("SNAP")
    assert found is not None
    assert found.id == "sl-1"
    assert found.status == "held"
    assert order_is_live_stop(found) is True
    params = provider._request.await_args.kwargs.get("params") or {}
    assert params.get("status") == "all"
    assert params.get("nested") == "true"


@pytest.mark.asyncio
async def test_find_working_stop_paginates():
    from services.alpaca_order_service import AlpacaOrderService

    page1 = [
        BrokerOrderResult(
            id="aapl-stop",
            symbol="AAPL",
            qty=1,
            side="sell",
            type="stop",
            status="held",
        )
    ]
    page2 = [
        BrokerOrderResult(
            id="snap-stop",
            symbol="SNAP",
            qty=1,
            side="sell",
            type="stop",
            status="held",
            raw={"stop_price": "5.48"},
        )
    ]
    inner = MagicMock()
    inner.last_next_page_token = "tok-1"
    svc = AlpacaOrderService(broker=inner)
    listed = AsyncMock(side_effect=[page1, page2])

    async def _list(status="all", limit=50, page_token=None):
        if page_token == "tok-1":
            inner.last_next_page_token = None
            return page2
        inner.last_next_page_token = "tok-1"
        return page1

    listed.side_effect = _list
    with patch.object(svc, "list_orders", listed):
        found = await svc.find_working_stop("SNAP")
    assert found is not None
    assert found.id == "snap-stop"
    assert listed.await_count == 2


@pytest.mark.asyncio
async def test_sync_from_alpaca_blocks_without_deposited_base():
    from domain.broker import BrokerAccount
    from services.portfolio_bootstrap_service import PortfolioBootstrapService
    from services.portfolio_service import PortfolioService

    svc = MagicMock(spec=PortfolioService)
    alpaca = MagicMock()
    alpaca.get_account = AsyncMock(
        return_value=BrokerAccount(cash=40, equity=100, portfolio_value=100)
    )
    alpaca.get_positions = AsyncMock(return_value=[])
    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=SimpleNamespace(amount=None, source="missing:DEPOSITED_BASE_USD")),
    ):
        out = await PortfolioBootstrapService(svc, alpaca).sync_from_alpaca()
    assert out is None
    svc.create.assert_not_called()


def _postgres_url() -> str | None:
    url = os.getenv("TEST_DATABASE_URL") or ""
    if "postgres" in url and "sqlite" not in url:
        return url
    return None


@pytest.mark.asyncio
@pytest.mark.skipif(not _postgres_url(), reason="no postgres in environment")
async def test_lease_and_attempt_on_postgres():
    from sqlalchemy.engine.url import make_url

    from database.models import Base
    from database.repositories.ops_repository import OpsFlagRepository
    from services.order_idempotency import bump_attempt, read_attempt

    raw = _postgres_url()
    assert raw
    url = str(raw)
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        a = await acquire_lease(session, name="pg_probe_lease", owner="pg-a", ttl_seconds=30)
        b = await acquire_lease(session, name="pg_probe_lease", owner="pg-b", ttl_seconds=30)
        assert a.acquired is True
        assert b.acquired is False
        flags = OpsFlagRepository(session)
        n = await bump_attempt(flags, "SNAP:stop:20261002")
        assert n >= 2
        assert await read_attempt(flags, "SNAP:stop:20261002") == n
    await engine.dispose()
