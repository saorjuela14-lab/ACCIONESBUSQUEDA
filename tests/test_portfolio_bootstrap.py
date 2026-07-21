"""Tests for portfolio bootstrap after ephemeral DB wipe."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.broker import BrokerAccount, BrokerPosition
from domain.entities import Portfolio, PortfolioPosition
from domain.enums import PortfolioMode, StrategyType
from services.portfolio_bootstrap_service import PortfolioBootstrapService
from services.portfolio_service import PortfolioService


@pytest.mark.asyncio
async def test_ensure_returns_existing():
    existing = Portfolio(
        name="CEO",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.REAL,
        initial_capital=50,
        cash=50,
    )
    svc = MagicMock(spec=PortfolioService)
    svc.list_all = AsyncMock(return_value=[existing])
    alpaca = MagicMock()
    alpaca.is_configured.return_value = True

    p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "existing"
    assert p.id == existing.id
    svc.create.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_syncs_from_alpaca_when_empty():
    created = Portfolio(
        name="Alpaca LIVE",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.REAL,
        initial_capital=100,
        cash=40,
    )
    mirrored = created.model_copy(
        update={
            "positions": [
                PortfolioPosition(ticker="F", shares=10, average_cost=6.0, current_price=6.1)
            ],
            "cash": 40.0,
        }
    )
    svc = MagicMock(spec=PortfolioService)
    svc.list_all = AsyncMock(return_value=[])
    svc.create = AsyncMock(return_value=created)
    svc.mirror_positions = AsyncMock(return_value=mirrored)

    alpaca = MagicMock()
    alpaca.is_configured.return_value = True
    alpaca.get_account = AsyncMock(
        return_value=BrokerAccount(cash=40, equity=100, portfolio_value=100, paper=False)
    )
    alpaca.get_positions = AsyncMock(
        return_value=[
            BrokerPosition(symbol="F", qty=10, avg_entry_price=6.0, current_price=6.1)
        ]
    )

    p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "alpaca"
    assert p.cash == 40.0
    assert len(p.positions) == 1
    svc.mirror_positions.assert_called_once()


@pytest.mark.asyncio
async def test_mirror_positions_does_not_debit_cash():
    repo = AsyncMock()
    portfolio = Portfolio(
        name="T",
        strategy=StrategyType.GROWTH,
        initial_capital=100,
        cash=100,
        positions=[],
    )
    repo.get_by_id = AsyncMock(return_value=portfolio)
    repo.update = AsyncMock(side_effect=lambda p: p)
    svc = PortfolioService(repo, MagicMock())
    out = await svc.mirror_positions(
        portfolio.id,
        positions=[PortfolioPosition(ticker="AAA", shares=2, average_cost=5)],
        cash=90,
        initial_capital=100,
    )
    assert out.cash == 90
    assert len(out.positions) == 1
    assert out.positions[0].ticker == "AAA"
