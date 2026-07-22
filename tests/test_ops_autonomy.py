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
        s.auto_execute_trades = True
        s.auto_execute_paper_first = True
        s.auto_execute_live = False
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        svc = AutoExecuteService(session, broker)
        ok, reason = svc.can_auto_trade()
        assert ok is True


@pytest.mark.asyncio
async def test_kill_switch_requires_confirm():
    from services.kill_switch_service import KillSwitchService

    session = MagicMock()
    with pytest.raises(ValueError):
        await KillSwitchService(session, MagicMock()).activate(confirm=False)
