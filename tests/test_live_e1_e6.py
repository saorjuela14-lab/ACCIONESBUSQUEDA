"""LIVE stocks E1–E6 blockers — entries stay closed (LIVE_ENTRIES_ENABLED=false)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings
from domain.broker import BrokerAccount, BrokerOrderRequest, ExecuteLine, ExecuteOrdersRequest
from domain.ops import PositionMandate
from services.live_cycle_lock import acquire_lease, live_client_order_id
from services.live_safety import (
    buy_thesis_blocked,
    exit_px_from_order_state,
    live_buys_allowed,
    protective_levels_from_order,
    record_stop_1r_block,
    reserve_entry_slots,
    sell_qty_exceeds_long,
    thesis_is_fresh_after_stop,
    voice_kill_off_confirmed,
)
from utils.market_hours import US_EASTERN


def test_live_entries_flag_stays_false():
    assert Settings.model_fields["live_entries_enabled"].default is False
    ok, why = live_buys_allowed(paper=False, live_entries_enabled=False)
    assert ok is False
    assert why == "live_entries_disabled"


# --- E1 ---


def test_e1_sell_qty_exceeds_long():
    assert sell_qty_exceeds_long(2, 1) is True
    assert sell_qty_exceeds_long(1, 1) is False
    assert sell_qty_exceeds_long(1, None) is True
    assert sell_qty_exceeds_long(0, 5) is True


@pytest.mark.asyncio
async def test_e1_submit_one_rejects_sell_above_long():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = False
    inner.is_configured.return_value = True
    inner.submit_order = AsyncMock(side_effect=AssertionError("must not submit"))
    svc = AlpacaOrderService(broker=inner)
    svc.get_positions = AsyncMock(return_value=[SimpleNamespace(symbol="SNAP", qty=1)])
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(symbol="SNAP", qty=2, side="sell", source_tag="test")
        )
    assert out.status == "failed"
    assert out.error == "sell_qty_exceeds_long"
    inner.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_e1_submit_one_buy_central_gates():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = False
    inner.is_configured.return_value = True
    inner.submit_order = AsyncMock()
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ), patch(
        "services.live_safety.live_entry_blocked", return_value=(False, "live_entries_enabled")
    ), patch.object(
        svc, "_live_buy_central_gates", AsyncMock(return_value="kill_switch_entries_blocked")
    ):
        out = await svc.submit_one(
            BrokerOrderRequest(symbol="AAPL", qty=1, side="buy", source_tag="test")
        )
    assert out.status == "failed"
    assert out.error == "kill_switch_entries_blocked"
    inner.submit_order.assert_not_called()


# --- E2 ---


@pytest.mark.asyncio
async def test_e2_reserve_slots_atomic_and_deny_on_fail():
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={"et_date": "2026-10-02", "count": 0, "symbols": []})
    flags.set_json = AsyncMock()
    with patch("services.live_cycle_lock.acquire_lease", AsyncMock(return_value=(True, {}))), patch(
        "services.live_cycle_lock.release_lease", AsyncMock()
    ):
        ok, why, data = await reserve_entry_slots(
            flags, n=1, max_entries=1, symbols=["SNAP"], today="2026-10-02"
        )
        assert ok is True
        assert why == "reserved"
        assert data["count"] == 1
        flags.set_json.assert_awaited()

        flags.get_json = AsyncMock(return_value={"et_date": "2026-10-02", "count": 1, "symbols": ["SNAP"]})
        ok, why, _ = await reserve_entry_slots(
            flags, n=1, max_entries=1, symbols=["AAPL"], today="2026-10-02"
        )
        assert ok is False
        assert why == "max_1_entry_per_day"

        async def _get_then_fail(name):
            if name == "live_entry_day_count":
                raise RuntimeError("db down")
            return {}

        flags.get_json = AsyncMock(side_effect=_get_then_fail)
        ok, why, _ = await reserve_entry_slots(flags, n=1, max_entries=1, today="2026-10-02")
        assert ok is False
        assert why == "entry_slot_write_failed"


@pytest.mark.asyncio
async def test_e2_reserve_denies_when_lock_fails():
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={})
    flags.set_json = AsyncMock()
    with patch("services.live_cycle_lock.acquire_lease", AsyncMock(return_value=(False, {"owner": "other"}))):
        ok, why, _ = await reserve_entry_slots(flags, n=1, max_entries=1)
    assert ok is False
    assert why == "entry_slot_lock_held"

    with patch("services.live_cycle_lock.acquire_lease", AsyncMock(side_effect=RuntimeError("lock err"))):
        ok, why, _ = await reserve_entry_slots(flags, n=1, max_entries=1)
    assert ok is False
    assert why == "entry_slot_lock_failed"


@pytest.mark.asyncio
async def test_e2_auto_execute_denies_when_budget_read_fails():
    from services.auto_execute_service import AutoExecuteService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = True
    flags = MagicMock()
    flags.get_json = AsyncMock(side_effect=RuntimeError("read fail"))
    with patch("services.auto_execute_service.get_settings") as gs, patch(
        "services.auto_execute_service.KillSwitchService"
    ) as KS, patch("database.repositories.ops_repository.OpsFlagRepository", return_value=flags):
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = True
        s.auto_execute_paper_first = False
        s.auto_execute_live = True
        s.auto_execute_require_market_open = False
        s.live_entries_enabled = True
        s.live_max_entries_per_day = 1
        gs.return_value = s
        KS.return_value.is_active = AsyncMock(return_value=False)
        svc = AutoExecuteService(session, broker)
        out = await svc.run_from_picks([], actor="test")
    assert out["skipped"] is True
    assert out["reason"] == "entry_budget_read_failed"


# --- E3 ---


def test_e3_session_wide_1r_requires_fresh_thesis():
    flag = record_stop_1r_block({}, "SNAP", r_mult=1.2, today="2026-10-01")
    flag["at"] = "2026-10-01T14:00:00-04:00"
    assert flag["session_blocked"] is True
    blocked, why = buy_thesis_blocked(flag, "AAPL", today="2026-10-01")
    assert blocked is True
    assert "session" in why
    assert thesis_is_fresh_after_stop(flag, None) is False
    stale = datetime(2026, 10, 1, 13, 0, tzinfo=US_EASTERN)
    assert thesis_is_fresh_after_stop(flag, stale) is False
    fresh = datetime(2026, 10, 1, 15, 0, tzinfo=US_EASTERN)
    assert thesis_is_fresh_after_stop(flag, fresh) is True
    assert buy_thesis_blocked(flag, "AAPL", today="2026-10-01", thesis_at=fresh)[0] is False


def test_e3_exit_px_from_order_state_without_fill():
    od = SimpleNamespace(
        side="sell",
        status="canceled",
        filled_qty=0,
        filled_avg_price=None,
        type="stop",
        raw={"type": "stop", "stop_price": "5.48"},
    )
    assert exit_px_from_order_state(od, SimpleNamespace(stop_loss=5.48)) == 5.48
    assert exit_px_from_order_state(None, SimpleNamespace(stop_loss=5.50)) == 5.50


@pytest.mark.asyncio
async def test_e3_cooldown_without_registered_fill():
    from services.position_lifecycle_service import PositionLifecycleService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.get_positions = AsyncMock(return_value=[])
    broker.latest_filled_sell = AsyncMock(return_value=None)
    broker.list_orders = AsyncMock(
        return_value=[
            SimpleNamespace(symbol="SNAP", type="stop", status="canceled", raw={"stop_price": "5.48"})
        ]
    )
    svc = PositionLifecycleService(session, broker)
    mandate = PositionMandate(
        id="m1",
        symbol="SNAP",
        qty=1,
        entry_price=6.0,
        stop_loss=5.48,
        take_profit=6.96,
        status="open",
    )
    svc._mandates.list_open = AsyncMock(return_value=[mandate])
    svc._mandates.save = AsyncMock()
    svc._record_stop_cooldown = AsyncMock()
    await svc.sync_mandates_from_broker()
    svc._record_stop_cooldown.assert_awaited()
    kwargs = svc._record_stop_cooldown.await_args
    assert kwargs.args[0] == "SNAP"


@pytest.mark.asyncio
async def test_e3_auto_execute_1r_check_fail_closed():
    from domain.daily_trade import TradePick
    from services.auto_execute_service import AutoExecuteService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = True
    broker.get_clock = AsyncMock(return_value=MagicMock(is_open=True))
    broker.get_account = AsyncMock(return_value=MagicMock(cash=21.0, equity=21.0, buying_power=21.0))
    broker.get_positions = AsyncMock(return_value=[])
    broker.execute = AsyncMock()

    flags = MagicMock()

    async def _get_json(name):
        if name == "live_stop_1r_block":
            raise RuntimeError("flag down")
        return {}

    flags.get_json = AsyncMock(side_effect=_get_json)
    flags.set_json = AsyncMock()
    with patch("services.auto_execute_service.get_settings") as gs, patch(
        "services.auto_execute_service.KillSwitchService"
    ) as KS, patch("services.risk_policy_service.RiskPolicyService") as RS, patch(
        "database.repositories.ops_repository.OpsFlagRepository", return_value=flags
    ), patch(
        "services.deposited_capital_service.get_deposited_base",
        AsyncMock(return_value=MagicMock(amount=21.76, source="env:DEPOSITED_BASE_USD", buy_allowed=True)),
    ):
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = True
        s.auto_execute_paper_first = False
        s.auto_execute_live = True
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        s.auto_execute_max_position_pct = 0.30
        s.auto_execute_max_risk_pct = 2.5
        s.auto_execute_micro_max_risk_pct = 4.0
        s.auto_execute_micro_max_open = 1
        s.lifecycle_micro_equity_usd = 50.0
        s.lifecycle_micro_default_stop_pct = 0.08
        s.lifecycle_micro_default_target_pct = 0.16
        s.intraday_only_enabled = False
        s.live_max_entries_per_day = 1
        gs.return_value = s
        KS.return_value.is_active = AsyncMock(return_value=False)
        RS.return_value.status = AsyncMock(
            return_value=MagicMock(macro=MagicMock(trading_allowed=True, mode="neutral", block_reason=None))
        )
        svc = AutoExecuteService(session, broker)
        pick = TradePick(
            ticker="AAPL",
            action="compra",
            current_price=2.0,
            committee_unanimous=True,
            sources=["committee"],
        )
        out = await svc.run_from_picks([pick], actor="test")
    assert out["skipped"] is True
    assert out["reason"] == "stop_1r_check_failed"
    broker.execute.assert_not_awaited()


# --- E4 ---


def test_e4_voice_exact_phrase_not_substring():
    assert voice_kill_off_confirmed({"confirm": True}, "confirma desactivar kill switch") is True
    assert voice_kill_off_confirmed({"confirm": True}, "confirma: NO apagues el kill-switch") is False
    assert voice_kill_off_confirmed({"confirm": True}, "confirma no apagues el kill switch") is False
    spoof = {
        "confirm": True,
        "user_text": "confirma desactivar kill switch",
    }
    assert voice_kill_off_confirmed(spoof, None) is False
    assert voice_kill_off_confirmed(spoof, "") is False
    assert voice_kill_off_confirmed(spoof, "cualquier otra cosa") is False


# --- E5 ---


@pytest.mark.asyncio
async def test_e5_execute_kill_and_brake_fail_closed():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = False
    inner.is_configured.return_value = True
    svc = AlpacaOrderService(broker=inner)
    req = ExecuteOrdersRequest(
        lines=[ExecuteLine(ticker="AAPL", shares=1, side="buy")],
        dry_run=False,
        confirm_live=True,
    )
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "database.engine.get_session", side_effect=RuntimeError("db")
    ):
        out = await svc.execute(req)
    assert not out.submitted
    assert "kill_switch_check_failed_fail_closed" in out.warnings

    async def _sessions():
        if False:
            yield None

    svc.get_account = AsyncMock(
        return_value=BrokerAccount(cash=20, buying_power=20, equity=20, paper=False)
    )
    svc.get_positions = AsyncMock(return_value=[])
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "database.engine.get_session", return_value=_sessions()
    ), patch(
        "services.deposited_capital_service.get_deposited_base",
        AsyncMock(side_effect=RuntimeError("base down")),
    ):
        out = await svc.execute(req)
    assert not out.submitted
    assert "deposited_brake_check_failed_fail_closed" in out.warnings


@pytest.mark.asyncio
async def test_e5_execute_missing_base_blocks_buys():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = False
    inner.is_configured.return_value = True
    svc = AlpacaOrderService(broker=inner)
    svc.get_account = AsyncMock(
        return_value=BrokerAccount(cash=20, buying_power=20, equity=20, paper=False)
    )
    svc.get_positions = AsyncMock(return_value=[])

    async def _sessions():
        sess = MagicMock()
        yield sess

    ks = MagicMock()
    ks.is_active = AsyncMock(return_value=False)
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "database.engine.get_session", return_value=_sessions()
    ), patch("services.kill_switch_service.KillSwitchService", return_value=ks), patch(
        "services.deposited_capital_service.get_deposited_base",
        AsyncMock(return_value=SimpleNamespace(amount=None, source="missing", buy_allowed=False)),
    ):
        out = await svc.execute(
            ExecuteOrdersRequest(
                lines=[ExecuteLine(ticker="AAPL", shares=1, side="buy")],
                dry_run=False,
                confirm_live=True,
            )
        )
    assert not out.submitted
    assert "deposited_base_missing" in out.warnings


# --- E6 ---


def test_e6_protective_levels_from_real_order():
    raw = {
        "symbol": "SNAP",
        "order_class": "bracket",
        "stop_loss": {"stop_price": "5.48"},
        "take_profit": {"limit_price": "6.96"},
        "legs": [
            {"type": "stop", "side": "sell", "stop_price": "5.48"},
            {"type": "limit", "side": "sell", "limit_price": "6.96"},
        ],
    }
    stop, tp = protective_levels_from_order(raw)
    assert stop == pytest.approx(5.48)
    assert tp == pytest.approx(6.96)
    # No invented 8/16
    assert stop != pytest.approx(6.0 * 0.92)


@pytest.mark.asyncio
async def test_e6_deferred_register_keeps_order_levels_not_defaults():
    from services.position_lifecycle_service import PositionLifecycleService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    pos = SimpleNamespace(symbol="SNAP", qty=1, avg_entry_price=6.0, current_price=6.1)
    broker.get_positions = AsyncMock(return_value=[pos])
    broker.list_orders = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="SNAP",
                raw={
                    "stop_loss": {"stop_price": "5.10"},
                    "take_profit": {"limit_price": "7.40"},
                },
            )
        ]
    )
    svc = PositionLifecycleService(session, broker)
    latest = PositionMandate(
        symbol="SNAP",
        qty=1,
        entry_price=6.0,
        stop_loss=5.10,
        take_profit=7.40,
        thesis="BUY gap-up",
        status="closed",
    )
    svc._mandates.list_open = AsyncMock(return_value=[])
    svc._mandates.get_latest = AsyncMock(return_value=latest)
    captured: dict = {}

    async def _reg(**kwargs):
        captured.update(kwargs)
        return PositionMandate(**{k: v for k, v in kwargs.items() if k != "apply_defaults"})

    svc.register_from_fill = AsyncMock(side_effect=_reg)
    await svc.sync_mandates_from_broker()
    assert captured.get("apply_defaults") is False
    assert captured.get("stop_loss") == pytest.approx(5.10)
    assert captured.get("take_profit") == pytest.approx(7.40)
    assert captured.get("thesis") == "BUY gap-up"
    assert captured.get("stop_loss") != pytest.approx(6.0 * 0.92)
    assert captured.get("take_profit") != pytest.approx(6.0 * 1.16)


# --- Voice clock recheck + replica lock + client_order_id ---


@pytest.mark.asyncio
async def test_voice_confirm_rechecks_session_clock():
    from services.voice_command_service import VoiceCommandService
    from services import voice_command_service as vcs

    vcs._set_pending(
        "p1",
        {"kind": "buy", "ticker": "AAPL", "shares": 1},
    )
    svc = VoiceCommandService()
    alpaca = MagicMock()
    alpaca.is_configured.return_value = True
    alpaca.paper = False
    alpaca.execute = AsyncMock(side_effect=AssertionError("must not execute after close"))
    with patch("services.voice_command_service.AlpacaOrderService", return_value=alpaca), patch(
        "services.voice_command_service._live_voice_symbol_guard", return_value=None
    ), patch("services.live_safety.eod_may_submit_orders", return_value=False):
        out = await svc._confirm(MagicMock(), {}, "p1")
    assert out.success is False
    assert out.params.get("blocked") == "after_regular_close_no_orders"
    alpaca.execute.assert_not_awaited()
    assert "p1" not in vcs._PENDING


@pytest.mark.asyncio
async def test_replica_lease_and_deterministic_client_order_id():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from database.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    async with factory() as session:
        got_a, _lease_a = await acquire_lease(None, owner="replica-a", now=now, session=session)
        assert got_a is True
        got_b, _ = await acquire_lease(None, owner="replica-b", now=now, session=session)
        assert got_b is False
    await engine.dispose()
    cid = live_client_order_id("SNAP", "buy", when=now)
    assert cid == "live-SNAP-20261002-buy-1"
    assert len(cid) <= 48
    again = live_client_order_id("SNAP", "buy", when=now)
    assert again == cid
    assert live_client_order_id("SNAP", "buy", when=now, attempt=2) == "live-SNAP-20261002-buy-2"


@pytest.mark.asyncio
async def test_autopilot_skips_when_replica_lease_held():
    from services.autopilot_service import AutopilotService

    session = MagicMock()
    svc = AutopilotService(session)
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={"owner": "other", "expires_at": "2099-01-01T00:00:00+00:00"})
    flags.set_json = AsyncMock()
    with patch("services.autopilot_service.OpsFlagRepository", return_value=flags), patch(
        "services.live_cycle_lock.acquire_lease",
        AsyncMock(return_value=(False, {"owner": "other", "backend": "desk_lease"})),
    ):
        out = await svc.run(actor="test")
    assert out.get("aborted") == "replica_lease_held" or out.get("skipped") is True
    assert "réplica" in (out.get("message") or "") or out.get("aborted") == "replica_lease_held"


@pytest.mark.asyncio
async def test_b4_central_gate_honors_own_reservation():
    """Reserve consumes the daily slot; the same cycle must still be allowed to buy."""
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
        inner.submit_order = AsyncMock(
            return_value={
                "id": "ord-res",
                "symbol": "SNAP",
                "qty": "1",
                "side": "buy",
                "status": "accepted",
                "client_order_id": "live-SNAP-res",
            }
        )
        svc = AlpacaOrderService(broker=inner)
        svc.get_account = AsyncMock(return_value=SimpleNamespace(equity=22.0))
        settings = SimpleNamespace(live_max_entries_per_day=1, deposited_brake_pct=5.0)
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
            blocked = await svc._live_buy_central_gates(settings, skip_daily_cap=False)
            allowed = await svc._live_buy_central_gates(settings, skip_daily_cap=True)
            out = await svc.submit_one(
                BrokerOrderRequest(
                    symbol="SNAP",
                    qty=1,
                    side="buy",
                    client_order_id="live-SNAP-res",
                    entry_slot_reserved=True,
                )
            )
    await engine.dispose()
    assert blocked == "max_1_entry_per_day"
    assert allowed is None
    assert out.error is None
    assert out.id == "ord-res"
    inner.submit_order.assert_awaited()
