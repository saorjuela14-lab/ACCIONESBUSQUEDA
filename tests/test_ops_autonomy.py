"""Tests for autonomous ops desk — lifecycle, kill switch, auto-execute, VaR/sector."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.ops import PortfolioRiskMetrics, PositionMandate, utc_now
from services.auto_execute_service import AutoExecuteService
from services.portfolio_risk_metrics_service import PortfolioRiskMetricsService
from services.position_lifecycle_service import PositionLifecycleService


def test_lifecycle_trailing_and_time_stop():
    svc = PositionLifecycleService.__new__(PositionLifecycleService)
    now = utc_now()
    m = PositionMandate(
        symbol="ABC",
        qty=1,
        entry_price=10,
        stop_loss=9.0,
        trailing_pct=0.1,
        peak_price=12.0,
        time_stop_days=5,
        opened_at=now - timedelta(days=6),
    )
    # time-stop should fire first
    action = PositionLifecycleService._evaluate(svc, m, price=11.5, now=now)
    assert action.action == "exit"
    assert "Time-stop" in action.reason

    m2 = PositionMandate(
        symbol="ABC",
        qty=1,
        entry_price=10,
        stop_loss=9.0,
        trailing_pct=0.1,
        peak_price=12.0,
        time_stop_days=30,
        opened_at=now - timedelta(days=1),
    )
    # price below trailing stop from peak 12 * 0.9 = 10.8
    action2 = PositionLifecycleService._evaluate(svc, m2, price=10.5, now=now)
    assert action2.action == "exit"
    assert "Stop" in action2.reason or "trailing" in action2.reason.lower()

    m3 = m2.model_copy(update={"thesis_invalidated": True, "invalidate_reason": "tesis rota"})
    action3 = PositionLifecycleService._evaluate(svc, m3, price=11.9, now=now)
    assert action3.action == "exit"
    assert "invalidada" in action3.reason.lower()


def test_lifecycle_take_profit_fires():
    svc = PositionLifecycleService.__new__(PositionLifecycleService)
    now = utc_now()
    m = PositionMandate(
        symbol="SNAP",
        qty=1,
        entry_price=4.78,
        stop_loss=4.54,
        take_profit=5.07,  # ~+6% micro target
        trailing_pct=0.05,
        peak_price=4.78,
        time_stop_days=3,
        opened_at=now - timedelta(days=1),
    )
    action = PositionLifecycleService._evaluate(svc, m, price=5.10, now=now)
    assert action.action == "exit"
    assert "Take-profit" in action.reason


def test_lifecycle_micro_time_stop_three_days():
    svc = PositionLifecycleService.__new__(PositionLifecycleService)
    now = utc_now()
    m = PositionMandate(
        symbol="AMC",
        qty=2,
        entry_price=2.69,
        stop_loss=2.55,
        take_profit=2.85,
        trailing_pct=0.05,
        peak_price=2.86,
        time_stop_days=3,
        opened_at=now - timedelta(days=3, hours=1),
    )
    action = PositionLifecycleService._evaluate(svc, m, price=2.79, now=now)
    assert action.action == "exit"
    assert "Time-stop" in action.reason


def test_sector_gate_blocks_overweight():
    metrics = PortfolioRiskMetrics(
        equity=1000,
        sector_weights={"Technology": 38.0},
        max_sector="Technology",
        max_sector_pct=38.0,
    )
    ok, reasons = PortfolioRiskMetricsService().gate_buy(
        metrics=metrics,
        symbol="AAPL",
        notional=100,  # +10% → 48%
        sector="Technology",
        beta=1.2,
        max_var_pct=8,
        max_beta=1.8,
        max_sector_pct=40,
    )
    assert ok is False
    assert any("Sector" in r for r in reasons)


def test_auto_execute_paper_first_blocks_live():
    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = False

    with patch("services.auto_execute_service.get_settings") as gs:
        s = MagicMock()
        s.firm_autonomy = False
        s.auto_execute_trades = True
        s.auto_execute_paper_first = True
        s.auto_execute_live = False
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        svc = AutoExecuteService(session, broker)
        ok, reason = svc.can_auto_trade()
        assert ok is False
        assert "LIVE" in reason or "paper" in reason.lower()


def test_auto_execute_allows_paper():
    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = True

    with patch("services.auto_execute_service.get_settings") as gs:
        s = MagicMock()
        s.firm_autonomy = False
        s.auto_execute_trades = True
        s.auto_execute_paper_first = True
        s.auto_execute_live = False
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        svc = AutoExecuteService(session, broker)
        ok, reason = svc.can_auto_trade()
        assert ok is True


def test_firm_autonomy_allows_live_without_paper_first():
    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = False

    with patch("services.auto_execute_service.get_settings") as gs:
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = False  # master switch still wins
        s.auto_execute_paper_first = True
        s.auto_execute_live = False
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        svc = AutoExecuteService(session, broker)
        ok, reason = svc.can_auto_trade()
        assert ok is True
        assert "firm_autonomy" in reason


@pytest.mark.asyncio
async def test_kill_switch_requires_confirm():
    from services.kill_switch_service import KillSwitchService

    session = MagicMock()
    with pytest.raises(ValueError):
        await KillSwitchService(session, MagicMock()).activate(confirm=False)


@pytest.mark.asyncio
async def test_auto_execute_skips_picks_without_committee_consensus():
    from domain.daily_trade import TradePick

    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = True
    broker.get_clock = AsyncMock(return_value=MagicMock(is_open=True))
    broker.get_account = AsyncMock(
        return_value=MagicMock(cash=21.68, equity=21.68, buying_power=21.68)
    )
    broker.execute = AsyncMock()

    with patch("services.auto_execute_service.get_settings") as gs, \
         patch("services.auto_execute_service.KillSwitchService") as KS, \
         patch("services.risk_policy_service.RiskPolicyService") as RS:
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = True
        s.auto_execute_paper_first = False
        s.auto_execute_live = True
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        KS.return_value.is_active = AsyncMock(return_value=False)
        RS.return_value.status = AsyncMock(
            return_value=MagicMock(
                macro=MagicMock(trading_allowed=True, mode="neutral", block_reason=None)
            )
        )
        svc = AutoExecuteService(session, broker)
        pick = TradePick(
            ticker="SOUN",
            action="compra",
            current_price=2.0,
            committee_unanimous=False,
            sources=["momentum"],
        )
        result = await svc.run_from_picks([pick], actor="test")
        assert result["skipped"] is True
        assert result["reason"] == "no_committee_consensus"
        broker.execute.assert_not_awaited()
