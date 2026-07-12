"""Portfolio mode and demo projection tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.entities import Portfolio, PortfolioPosition
from domain.enums import PortfolioMode, StrategyType
from services.demo_projection_service import DemoProjectionService


@pytest.fixture
def demo_portfolio():
    return Portfolio(
        id="demo-1",
        name="Demo PF",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.DEMO,
        initial_capital=5000,
        cash=3000,
        positions=[
            PortfolioPosition(ticker="ABBV", shares=10, average_cost=180, current_price=185),
        ],
    )


@pytest.mark.asyncio
async def test_demo_projection_returns_scenarios(demo_portfolio):
    market = MagicMock()
    market.get_history = AsyncMock(return_value=MagicMock(empty=False, __getitem__=lambda s, k: MagicMock(
        pct_change=lambda: MagicMock(dropna=lambda: __import__("pandas").Series([0.01, -0.005, 0.002] * 30))
    )))
    market.get_history.return_value.empty = False
    import pandas as pd
    closes = pd.Series([100 + i * 0.5 for i in range(60)])
    df = pd.DataFrame({"Close": closes})
    market.get_history = AsyncMock(return_value=df)

    svc = DemoProjectionService(market)
    report = await svc.project(demo_portfolio, horizon_months=6)
    assert report.mode == "demo"
    assert len(report.points) == 7
    assert len(report.scenarios) == 3
    assert "Monte Carlo" in report.summary
    assert report.scenarios[1].projected_value > 0


def test_portfolio_mode_defaults_real():
    p = Portfolio(name="X", strategy=StrategyType.GROWTH, initial_capital=100, cash=100)
    assert p.mode == PortfolioMode.REAL
