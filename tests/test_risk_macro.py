"""Tests for Risk Desk + macro regime."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.risk import PortfolioRiskSnapshot, RiskPolicy
from services.macro_regime_service import MacroRegimeService
from services.risk_policy_service import RiskPolicyService


@pytest.mark.asyncio
async def test_macro_regime_crisis_on_high_vix():
    macro_provider = MagicMock()
    macro_provider.get_macro_snapshot = AsyncMock(
        return_value={
            "fred": {
                "FED_FUNDS": {"value": 5.5, "date": "2026-01-01"},
                "CPI_YOY": {"value": 4.5, "date": "2026-01-01"},
                "YIELD_CURVE": {"value": -0.8, "date": "2026-01-01"},
            },
            "indicators": {"VIX": 35.0},
        }
    )
    svc = MacroRegimeService(macro_provider)
    assessment = await svc.assess(market_regime="bearish")
    assert assessment.mode in ("risk_off", "crisis")
    assert assessment.size_multiplier < 1.0
    assert assessment.vix == 35.0
    assert assessment.risks


@pytest.mark.asyncio
async def test_macro_regime_risk_on_calm():
    macro_provider = MagicMock()
    macro_provider.get_macro_snapshot = AsyncMock(
        return_value={
            "fred": {
                "FED_FUNDS": {"value": 2.0, "date": "2026-01-01"},
                "CPI_YOY": {"value": 2.1, "date": "2026-01-01"},
                "YIELD_CURVE": {"value": 0.8, "date": "2026-01-01"},
                "UNEMPLOYMENT": {"value": 3.5, "date": "2026-01-01"},
            },
            "indicators": {"VIX": 12.0},
        }
    )
    svc = MacroRegimeService(macro_provider)
    assessment = await svc.assess(market_regime="bullish")
    assert assessment.mode in ("risk_on", "neutral")
    assert assessment.size_multiplier >= 1.0
    assert assessment.trading_allowed is True


def test_risk_blocks_crisis_buys():
    risk = RiskPolicyService()
    policy = RiskPolicy(crisis_block_buys=True)
    verdict = risk.evaluate_buy(
        symbol="AAPL",
        qty=1,
        price=100,
        stop_loss=92,
        take_profit=112,
        policy=policy,
        macro_mode="crisis",
        size_multiplier=0,
        portfolio=PortfolioRiskSnapshot(equity=1000, cash=500, cash_pct=50),
        trading_allowed=False,
        block_reason="crisis",
    )
    assert verdict.allowed is False


def test_risk_enforces_cash_reserve():
    risk = RiskPolicyService()
    policy = RiskPolicy(cash_reserve_pct=50, max_position_pct=90, require_stop_loss=False)
    # equity 100, cash 60 → max spend 10 after 50% reserve
    portfolio = PortfolioRiskSnapshot(
        equity=100,
        cash=60,
        cash_pct=60,
        invested_pct=40,
        open_positions=0,
    )
    verdict = risk.evaluate_buy(
        symbol="XYZ",
        qty=2,
        price=20,  # $40 notional > $10 max spend
        stop_loss=18,
        take_profit=24,
        policy=policy,
        macro_mode="neutral",
        size_multiplier=1.0,
        portfolio=portfolio,
    )
    assert verdict.allowed is False or (verdict.adjusted_qty is not None and verdict.adjusted_qty < 2)


def test_risk_daily_loss_kill_switch():
    risk = RiskPolicyService()
    policy = RiskPolicy(max_daily_loss_pct=5.0, require_stop_loss=False)
    portfolio = PortfolioRiskSnapshot(
        equity=1000,
        cash=500,
        day_pl_pct=-6.0,
    )
    verdict = risk.evaluate_buy(
        symbol="XYZ",
        qty=1,
        price=10,
        stop_loss=9,
        take_profit=12,
        policy=policy,
        macro_mode="neutral",
        size_multiplier=1.0,
        portfolio=portfolio,
    )
    assert verdict.allowed is False
    assert any("diaria" in r.lower() or "kill" in r.lower() for r in verdict.reasons)


def test_filter_picks_crisis_empties():
    risk = RiskPolicyService()

    class P:
        def __init__(self):
            self.score = 80
            self.risks = []
            self.confidence = 0.8

        def model_copy(self, update=None):
            return self

    assert risk.filter_picks_for_regime([P()], size_multiplier=0, mode="crisis") == []
