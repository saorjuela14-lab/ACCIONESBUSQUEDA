"""LIVE stocks-desk safety: exits-only, deposited brake, journal, autopilot caps."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings
from services.live_safety import (
    blocked_buy_symbols,
    buy_thesis_blocked,
    deposited_brake_floor,
    deposited_brake_triggered,
    entry_day_allowed,
    entry_price_from_fill,
    eod_may_submit_orders,
    filled_exit_price_from_order,
    is_us_equity_live_symbol,
    live_buys_allowed,
    live_entry_blocked,
    next_et_session_date,
    order_looks_like_stop,
    remaining_entry_slots,
    r_multiple_loss,
    record_entry_day_fill,
    record_stop_1r_block,
    skip_reopen_after_hours_close,
    submit_already_paused,
    submit_fail_pause,
    voice_kill_off_confirmed,
)
from utils.market_hours import US_EASTERN


def test_deposited_brake_floor_2176():
    assert deposited_brake_floor(21.76, 5.0) == 20.67
    assert deposited_brake_triggered(20.67, 21.76, 5.0) is True
    assert deposited_brake_triggered(20.68, 21.76, 5.0) is False
    assert deposited_brake_triggered(21.76, 21.76, 5.0) is False
    assert deposited_brake_triggered(None, 21.76) is False


def test_live_buys_gate():
    ok, why = live_buys_allowed(paper=True, live_entries_enabled=False)
    assert ok is True
    assert why == "paper_entries_ok"
    ok, why = live_buys_allowed(paper=False, live_entries_enabled=False)
    assert ok is False
    assert why == "live_entries_disabled"
    ok, _ = live_buys_allowed(paper=False, live_entries_enabled=True)
    assert ok is True


def test_safe_settings_defaults_and_app_env():
    """Code defaults are fail-safe; production opts in via env."""
    s = Settings.model_validate({
        "firm_autonomy": False,
        "auto_execute_trades": False,
        "auto_execute_live": False,
        "alpaca_paper": True,
        "live_entries_enabled": False,
        "app_env": "prod",
    })
    assert s.app_env == "production"
    assert s.live_entries_enabled is False
    assert s.firm_autonomy is False
    assert Settings.model_fields["alpaca_paper"].default is True
    assert Settings.model_fields["firm_autonomy"].default is False
    assert Settings.model_fields["live_entries_enabled"].default is False


def test_app_env_aliases(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "PRODUCTION")
    # Don't construct global get_settings; use a fresh model
    s = Settings.model_validate({"app_env": "PRODUCTION"})
    assert s.app_env == "production"
    s2 = Settings.model_validate({"app_env": "prod"})
    assert s2.app_env == "production"


def test_us_equity_whitelist():
    assert is_us_equity_live_symbol("SNAP")
    assert is_us_equity_live_symbol("AAPL")
    assert not is_us_equity_live_symbol("BONK")
    assert not is_us_equity_live_symbol("BTC/USD")
    assert not is_us_equity_live_symbol("ETHUSD")


def test_voice_kill_off_requires_explicit_human():
    assert voice_kill_off_confirmed({"confirm": True}, "apaga el kill switch") is False
    assert voice_kill_off_confirmed({"enabled": False}, "confirma desactivar kill switch") is False
    assert voice_kill_off_confirmed(
        {"confirm": True},
        "Sergio confirma desactivar kill switch",
    ) is True
    assert voice_kill_off_confirmed({"confirm": True}, "") is False
    # False positive: "skill" contains "kill"; "apag" is not a kill-switch phrase.
    assert voice_kill_off_confirmed({"confirm": True}, "Sergio confirma el skill de voz") is False
    assert voice_kill_off_confirmed({"confirm": True}, "Sergio confirma apagar el skill") is False
    assert voice_kill_off_confirmed({"confirm": True}, "apaga todo") is False
    assert voice_kill_off_confirmed({"confirm": True}, "kill") is False


def test_entry_price_never_uses_stop():
    assert entry_price_from_fill(None, filled_qty=0, stop_loss=5.48) is None
    assert entry_price_from_fill(0, filled_qty=1, limit_price=6.0, stop_loss=5.48) is None
    assert entry_price_from_fill(6.12, filled_qty=1, stop_loss=5.48) == 6.12


def test_filled_exit_ignores_working_stop():
    working = SimpleNamespace(side="sell", status="new", filled_qty=0, filled_avg_price=None, type="stop")
    assert filled_exit_price_from_order(working) is None
    filled = SimpleNamespace(
        side="sell", status="filled", filled_qty=1, filled_avg_price=5.51, type="stop"
    )
    assert filled_exit_price_from_order(filled) == 5.51
    assert order_looks_like_stop(filled) is True


def test_skip_reopen_after_hours():
    closed = datetime(2026, 10, 1, 16, 20, tzinfo=US_EASTERN)
    now = datetime(2026, 10, 1, 17, 0, tzinfo=US_EASTERN)
    assert skip_reopen_after_hours_close(closed, now) is True
    next_day = datetime(2026, 10, 2, 10, 0, tzinfo=US_EASTERN)
    assert skip_reopen_after_hours_close(closed, next_day) is False
    midday = datetime(2026, 10, 1, 14, 0, tzinfo=US_EASTERN)
    now_mid = datetime(2026, 10, 1, 14, 5, tzinfo=US_EASTERN)
    assert skip_reopen_after_hours_close(midday, now_mid) is False


def test_eod_no_orders_after_close():
    assert eod_may_submit_orders(datetime(2026, 10, 1, 15, 50, tzinfo=US_EASTERN)) is True
    assert eod_may_submit_orders(datetime(2026, 10, 1, 16, 0, tzinfo=US_EASTERN)) is False
    assert eod_may_submit_orders(datetime(2026, 10, 1, 16, 1, tzinfo=US_EASTERN)) is False


def test_autopilot_max_one_entry_and_fail_pause():
    ok, why, flag = entry_day_allowed({}, max_entries=1, today="2026-10-01")
    assert ok is True
    flag = record_entry_day_fill(flag, "SNAP")
    ok, why, flag = entry_day_allowed(flag, max_entries=1, today="2026-10-01")
    assert ok is False
    assert why == "max_1_entry_per_day"
    ok, _, flag = entry_day_allowed(flag, max_entries=1, today="2026-10-02")
    assert ok is True

    paused, _, st = submit_fail_pause(
        {}, submitted=0, failed=1, max_fails=3, today="2026-10-01"
    )
    assert paused is False
    paused, _, st = submit_fail_pause(st, submitted=0, failed=1, max_fails=3, today="2026-10-01")
    assert paused is False
    paused, why, st = submit_fail_pause(st, submitted=0, failed=1, max_fails=3, today="2026-10-01")
    assert paused is True
    assert why == "submit_fail_pause"
    assert submit_already_paused(st, today="2026-10-01") is True
    assert submit_already_paused(st, today="2026-10-02") is False
    paused, _, st = submit_fail_pause(st, submitted=1, failed=0, max_fails=3, today="2026-10-01")
    assert paused is False


@pytest.mark.asyncio
async def test_kill_activate_without_flatten_keeps_broker():
    from services.kill_switch_service import KillSwitchService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = False
    broker.cancel_all_orders = AsyncMock()
    broker.close_all_positions = AsyncMock()

    flags = MagicMock()
    flags.set_kill_switch = AsyncMock()
    audit = MagicMock()
    audit.record = AsyncMock()

    with patch("services.kill_switch_service.OpsFlagRepository", return_value=flags), \
         patch("services.kill_switch_service.AuditService", return_value=audit):
        state = await KillSwitchService(session, broker).activate(
            reason="freno 5%",
            actor="test",
            flatten=False,
            confirm=True,
        )
    assert state.active is True
    assert state.flat_attempted is False
    broker.cancel_all_orders.assert_not_awaited()
    broker.close_all_positions.assert_not_awaited()


@pytest.mark.asyncio
async def test_arm_deposited_brake_no_flatten():
    from services.live_safety import arm_deposited_brake_if_needed

    session = MagicMock()
    broker = MagicMock()
    ks = MagicMock()
    ks.is_active = AsyncMock(return_value=False)
    ks.activate = AsyncMock(
        return_value=SimpleNamespace(
            model_dump=lambda mode="json": {"active": True, "flat_attempted": False}
        )
    )
    with patch("services.kill_switch_service.KillSwitchService", return_value=ks):
        out = await arm_deposited_brake_if_needed(
            session, broker, equity=20.50, base=21.76, pct=5.0
        )
    assert out and out["armed"] is True
    assert out["flatten"] is False
    ks.activate.assert_awaited()
    kwargs = ks.activate.await_args.kwargs
    assert kwargs["flatten"] is False
    assert kwargs["confirm"] is True


@pytest.mark.asyncio
async def test_viernes_cannot_disable_kill_without_phrase():
    from services.voice_assistant_service import VoiceAssistantService

    svc = VoiceAssistantService()
    db = MagicMock()
    ks = MagicMock()
    ks.deactivate = AsyncMock()
    ks.activate = AsyncMock()
    with patch("services.kill_switch_service.KillSwitchService", return_value=ks):
        result, _ = await svc._dispatch_tool(
            "set_kill_switch",
            {"enabled": False, "confirm": True},
            db,
            portfolio_id=None,
            user_text="apaga el kill",
        )
    assert result["ok"] is False
    assert result.get("requires_confirm") is True
    ks.deactivate.assert_not_awaited()

    with patch("services.kill_switch_service.KillSwitchService", return_value=ks):
        result2, _ = await svc._dispatch_tool(
            "set_kill_switch",
            {"enabled": False, "confirm": True},
            db,
            portfolio_id=None,
            user_text="confirma desactivar kill switch",
        )
    assert result2["ok"] is True
    ks.deactivate.assert_awaited()


@pytest.mark.asyncio
async def test_intraday_flat_skips_after_close():
    from services.intraday_flat_service import IntradayFlatService

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.get_positions = AsyncMock(side_effect=AssertionError("no broker after close"))
    svc = IntradayFlatService(session, broker)
    svc._settings = SimpleNamespace(intraday_only_enabled=True)
    with patch(
        "services.intraday_flat_service.eod_may_submit_orders", return_value=False
    ), patch.object(svc, "should_flat_now", return_value=(True, "eod")):
        out = await svc.run(actor="test")
    assert out["skipped"] is True
    assert out["reason"] == "after_regular_close_no_orders"


@pytest.mark.asyncio
async def test_autopilot_kill_still_runs_lifecycle():
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

    with patch("services.autopilot_service.KillSwitchService") as KS, \
         patch("services.autopilot_service.ReconcileService", return_value=recon), \
         patch("services.autopilot_service.PositionLifecycleService", return_value=life), \
         patch("services.autopilot_service.OpsFlagRepository", return_value=flags), \
         patch("services.live_safety.arm_deposited_brake_if_needed", AsyncMock(return_value=None)), \
         patch(
             "services.deposited_capital_service.resolve_trading_base",
             AsyncMock(return_value=SimpleNamespace(amount=21.76)),
         ), \
         patch("services.intraday_flat_service.IntradayFlatService") as Flat, \
         patch("services.risk_policy_service.RiskPolicyService") as Risk, \
         patch("services.holdings_strategy_review_service.HoldingsStrategyReviewService") as Hold:
        KS.return_value.is_active = AsyncMock(return_value=True)
        Flat.return_value.run = AsyncMock(return_value={"skipped": True})
        Risk.return_value.status = AsyncMock(
            return_value=SimpleNamespace(
                macro=SimpleNamespace(mode="neutral", trading_allowed=True, thesis="", size_multiplier=1)
            )
        )
        Hold.return_value.review = AsyncMock(return_value={})
        out = await svc.run(actor="test")
    assert out.get("aborted") != "kill_switch_active"
    assert out["kill_switch"] == "active_exits_only"
    assert out["auto_execute"]["reason"] == "exits_only_cycle"
    life.scan.assert_awaited()


def test_production_without_alpaca_mode_env_is_unconfigured():
    from services.live_safety import (
        production_trading_unconfigured,
        trading_mode_label,
    )

    s = Settings.model_validate({"app_env": "production", "alpaca_paper": True})
    assert production_trading_unconfigured(s, environ={}) is True
    assert trading_mode_label(s, environ={}) == "unconfigured"
    assert production_trading_unconfigured(s, environ={"ALPACA_PAPER": "true"}) is False
    assert trading_mode_label(s, environ={"ALPACA_PAPER": "true"}) == "paper"
    live = Settings.model_validate(
        {"app_env": "production", "alpaca_paper": False, "alpaca_live_trade": True}
    )
    assert trading_mode_label(live, environ={"ALPACA_LIVE_TRADE": "true"}) == "live"


def test_factory_refuses_silent_paper_in_production(monkeypatch):
    from config.settings import get_settings
    from providers.broker.factory import get_broker_provider

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    monkeypatch.delenv("ALPACA_LIVE_TRADE", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "CKXXXXLIVE")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret-live")
    get_settings.cache_clear()
    try:
        broker = get_broker_provider()
        assert broker.is_configured() is False
        assert broker._api_key == ""
        assert broker._secret_key == ""
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_execute_blocked_when_trading_unconfigured():
    from domain.broker import ExecuteLine, ExecuteOrdersRequest
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.is_configured.return_value = True
    inner.paper = False
    inner.submit_order = AsyncMock(side_effect=AssertionError("no orders"))
    svc = AlpacaOrderService(broker=inner)
    with patch("services.live_safety.production_trading_unconfigured", return_value=True):
        out = await svc.execute(
            ExecuteOrdersRequest(
                lines=[ExecuteLine(ticker="SNAP", shares=1, side="buy")],
                dry_run=False,
                confirm_live=True,
            )
        )
    assert out.submitted == []
    assert any("trading_mode_unconfigured" in w for w in out.warnings)
    inner.submit_order.assert_not_called()


@pytest.mark.asyncio
async def test_health_includes_trading_mode_unconfigured():
    from apis.routes.health import health_check

    s = MagicMock()
    s.app_env = "production"
    s.whatsapp_briefing_enabled = False
    s.effective_alpaca_paper = True
    with patch("apis.routes.health.db_snapshot", return_value={"ready": True, "host": None, "error": None}), \
         patch("apis.routes.health.get_settings", return_value=s), \
         patch("services.live_safety.trading_mode_label", return_value="unconfigured"):
        out = await health_check()
    assert out["trading_mode"] == "unconfigured"
    assert out["trading_mode_unconfigured"] is True
    assert out["status"] == "healthy"


def test_live_entry_blocked_buys_not_exits():
    blocked, why = live_entry_blocked(side="buy", paper=False, live_entries_enabled=False)
    assert blocked is True
    assert why == "live_entries_disabled"
    blocked, why = live_entry_blocked(side="sell", paper=False, live_entries_enabled=False)
    assert blocked is False
    blocked, _ = live_entry_blocked(side="buy", paper=True, live_entries_enabled=False)
    assert blocked is False


def test_entry_day_counts_each_fill_not_the_batch():
    _, _, flag = entry_day_allowed({}, max_entries=2, today="2026-10-01")
    flag = record_entry_day_fill(flag, "AAA")
    flag = record_entry_day_fill(flag, "BBB")
    assert flag["count"] == 2
    assert "at" in flag
    ok, why, flag = entry_day_allowed(flag, max_entries=2, today="2026-10-01")
    assert ok is False
    assert why == "max_1_entry_per_day"
    # A 2-buy batch with cap=1 has 1 remaining slot — never submit both then count as 1.
    empty = {"et_date": "2026-10-01", "count": 0, "symbols": []}
    assert remaining_entry_slots(empty, max_entries=1, today="2026-10-01") == 1
    one = record_entry_day_fill(dict(empty), "AAA")
    assert remaining_entry_slots(one, max_entries=1, today="2026-10-01") == 0


def test_stop_1r_blocks_buy_thesis_next_session():
    assert r_multiple_loss(10.0, 9.2, 9.2) == pytest.approx(1.0)
    assert r_multiple_loss(10.0, 9.2, 9.5) < 1.0
    flag = record_stop_1r_block({}, "SNAP", r_mult=1.1, today="2026-10-01")  # Thursday
    until = next_et_session_date(__import__("datetime").date(2026, 10, 1)).isoformat()
    assert until == "2026-10-02"
    blocked, why = buy_thesis_blocked(flag, "SNAP", today="2026-10-01")
    assert blocked is True
    blocked, _ = buy_thesis_blocked(flag, "SNAP", today="2026-10-02")
    assert blocked is True
    blocked, _ = buy_thesis_blocked(flag, "SNAP", today="2026-10-03")
    assert blocked is False
    assert "SNAP" in blocked_buy_symbols(flag, today="2026-10-02")
    assert buy_thesis_blocked(flag, "AAPL", today="2026-10-02")[0] is False


def test_kill_switch_request_flatten_defaults_false():
    from apis.routes.ops import KillSwitchRequest

    body = KillSwitchRequest(confirm=True, reason="test")
    assert body.flatten is False


@pytest.mark.asyncio
async def test_submit_one_live_buy_blocked_sell_allowed(monkeypatch):
    from domain.broker import BrokerOrderRequest
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.is_configured.return_value = True
    inner.paper = False
    inner.last_request_id = "rid-1"
    inner.submit_order = AsyncMock(
        return_value={"id": "s1", "status": "accepted", "symbol": "SNAP", "side": "sell", "qty": "1"}
    )
    svc = AlpacaOrderService(broker=inner)
    monkeypatch.setenv("LIVE_ENTRIES_ENABLED", "false")
    from config.settings import get_settings

    get_settings.cache_clear()
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        buy = await svc.submit_one(
            BrokerOrderRequest(symbol="SNAP", qty=1, side="buy", source_tag="test")
        )
        sell = await svc.submit_one(
            BrokerOrderRequest(symbol="SNAP", qty=1, side="sell", source_tag="test")
        )
    assert buy.error == "live_entries_disabled"
    assert buy.status == "failed"
    inner.submit_order.assert_awaited_once()  # only the sell
    assert sell.error is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_replace_stop_does_not_submit_if_list_orders_fails():
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.is_configured.return_value = True
    inner.paper = False
    inner.submit_order = AsyncMock(side_effect=AssertionError("no naked stop"))
    svc = AlpacaOrderService(broker=inner)
    svc.list_orders = AsyncMock(side_effect=RuntimeError("timeout"))
    with patch("services.live_safety.production_trading_unconfigured", return_value=False), patch(
        "utils.market_hours.eod_may_submit_orders", return_value=True
    ):
        out = await svc.replace_protective_stop(symbol="SNAP", qty=1, stop_price=5.0)
    assert out is not None
    assert out.error == "list_orders_failed_stop_intact"
    inner.submit_order.assert_not_called()


def test_source_tag_in_client_order_id():
    from domain.broker import BrokerOrderRequest
    from services.alpaca_order_service import AlpacaOrderService

    inner = MagicMock()
    inner.paper = True
    svc = AlpacaOrderService(broker=inner)
    payload = svc._build_order_payload(
        BrokerOrderRequest(symbol="AAPL", qty=1, side="buy", source_tag="voice")
    )
    assert payload["client_order_id"].startswith("voice-")


@pytest.mark.asyncio
async def test_ops_status_exits_only_allowed_false_for_entries():
    from apis.routes.ops import ops_status

    session = MagicMock()
    ks_state = MagicMock()
    ks_state.model_dump.return_value = {"active": False}
    auto_policy = MagicMock()
    auto_policy.model_dump.return_value = {}
    s = SimpleNamespace(
        firm_autonomy=True,
        effective_autopilot_interval_minutes=10,
        lifecycle_enabled=True,
        lifecycle_auto_exit=True,
        holdings_strategy_review_enabled=True,
        intraday_only_enabled=False,
        intraday_flat_minutes_before_close=20,
        intraday_flat_cron="",
        intraday_flat_winners_only=False,
        intraday_flat_min_pnl_pct=0.0,
        intraday_2r_hold_enabled=True,
        intraday_carry_max_loss_pct=8.0,
        live_entries_enabled=False,
        live_max_entries_per_day=1,
        live_submit_fail_pause=3,
        deposited_brake_pct=5.0,
        app_env="production",
        auto_execute_max_risk_pct=2.5,
        auto_execute_micro_max_risk_pct=2.5,
        auto_execute_post_stop_cooldown_minutes=90,
        auto_execute_max_position_pct=0.30,
        lifecycle_trail_arm_profit_pct=0.05,
        lifecycle_micro_default_stop_pct=0.08,
        lifecycle_micro_default_target_pct=0.16,
        lifecycle_micro_trailing_pct=0.10,
        reconcile_auto_sync=False,
        risk_max_var_pct=5.0,
        risk_max_portfolio_beta=1.5,
        risk_max_sector_pct=40.0,
    )
    auto = MagicMock()
    auto.can_auto_trade_async = AsyncMock(return_value=(False, "live_entries_disabled"))
    auto.policy.return_value = auto_policy
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={})
    with (
        patch("apis.routes.ops.get_settings", return_value=s),
        patch("apis.routes.ops.KillSwitchService") as ks_cls,
        patch("apis.routes.ops.AutoExecuteService", return_value=auto),
        patch("apis.routes.ops.OpsFlagRepository", return_value=flags),
        patch("apis.routes.ops.trading_mode_label", return_value="live"),
    ):
        ks_cls.return_value.status = AsyncMock(return_value=ks_state)
        out = await ops_status(session)
    ae = out["auto_execute"]
    assert ae["allowed"] is False
    assert ae["entries_allowed"] is False
    assert ae["exits_only"] is True
    assert ae["reason"] == "live_entries_disabled"
    assert out["live_entries_enabled"] is False
