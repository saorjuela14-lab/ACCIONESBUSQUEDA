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


def test_kill_switch_and_sizing_vs_deposited_2176():
    from domain.firm_capital import return_pct_from_base

    risk = RiskPolicyService()
    policy = RiskPolicy(
        max_daily_loss_pct=5.0,
        max_position_pct=35.0,
        cash_reserve_pct=10.0,
        require_stop_loss=False,
    )
    base = 21.76
    # 5% of $21.76 = $1.088 → equity 20.50 is −5.79% → block
    underwater = PortfolioRiskSnapshot(
        equity=20.50,
        cash=15.0,
        capital_base=base,
        day_pl_pct=return_pct_from_base(20.50, base),
        open_positions=0,
    )
    blocked = risk.evaluate_buy(
        symbol="SNAP",
        qty=1,
        price=7.0,
        stop_loss=6.44,
        take_profit=8.12,
        policy=policy,
        macro_mode="neutral",
        size_multiplier=1.0,
        portfolio=underwater,
    )
    assert blocked.allowed is False
    assert any("kill" in r.lower() or "depositado" in r.lower() for r in blocked.reasons)

    # −3.49% vs deposits stays under 5% — allow (qty may still size vs 35% of 21.76 = $7.62)
    ok_book = PortfolioRiskSnapshot(
        equity=21.00,
        cash=15.0,
        capital_base=base,
        day_pl_pct=return_pct_from_base(21.00, base),
        open_positions=0,
    )
    allowed = risk.evaluate_buy(
        symbol="SNAP",
        qty=1,
        price=7.0,
        stop_loss=6.44,
        take_profit=8.12,
        policy=policy,
        macro_mode="neutral",
        size_multiplier=1.0,
        portfolio=ok_book,
    )
    assert allowed.allowed is True
    assert round(base * 0.35, 2) == 7.62
    # 5% kill $ vs deposited: $1.088 (was $1.00 on the $20 stamp)
    assert round(base * 0.05, 3) == 1.088


def test_sizing_blocks_when_deposited_base_and_equity_missing():
    risk = RiskPolicyService()
    policy = RiskPolicy(require_stop_loss=False)
    portfolio = PortfolioRiskSnapshot(equity=0, cash=0, capital_base=None)
    verdict = risk.evaluate_buy(
        symbol="SNAP",
        qty=1,
        price=7.0,
        stop_loss=6.44,
        take_profit=8.12,
        policy=policy,
        macro_mode="neutral",
        size_multiplier=1.0,
        portfolio=portfolio,
    )
    assert verdict.allowed is False
    assert any("20" in r or "conservador" in r.lower() or "depositad" in r.lower() for r in verdict.reasons)


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
