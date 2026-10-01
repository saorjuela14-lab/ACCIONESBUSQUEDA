"""Tests for portfolio bootstrap after ephemeral DB wipe."""

from unittest.mock import AsyncMock, MagicMock, patch

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

    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=MagicMock(amount=None, source="unavailable")),
    ):
        p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "existing"
    assert p.id == existing.id
    svc.create.assert_not_called()


@pytest.mark.asyncio
async def test_existing_migrates_initial_capital_to_deposited():
    existing = Portfolio(
        name="CEO",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.REAL,
        initial_capital=20.0,
        cash=15.87,
    )
    migrated = existing.model_copy(update={"initial_capital": 21.76})
    svc = MagicMock(spec=PortfolioService)
    svc.list_all = AsyncMock(return_value=[existing])
    svc.mirror_positions = AsyncMock(return_value=migrated)
    alpaca = MagicMock()
    alpaca.is_configured.return_value = True
    deposited = MagicMock(amount=21.76, source="alpaca")
    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=deposited),
    ):
        p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "existing"
    assert p.initial_capital == 21.76
    svc.mirror_positions.assert_called_once()
    assert svc.mirror_positions.await_args.kwargs["initial_capital"] == 21.76
    # cash/positions preserved
    assert svc.mirror_positions.await_args.kwargs["cash"] == 15.87


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

    deposited = MagicMock(amount=21.76, source="alpaca", deposits=21.76, withdrawals=0, activity_count=1)
    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=deposited),
    ):
        p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "alpaca"
    assert p.cash == 40.0
    assert len(p.positions) == 1
    svc.create.assert_called_once()
    assert svc.create.await_args.kwargs["initial_capital"] == 21.76
    svc.mirror_positions.assert_called_once()
    assert svc.mirror_positions.await_args.kwargs["initial_capital"] == 21.76


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


@pytest.mark.asyncio
async def test_compute_metrics_drawdown_vs_deposited_base():
    repo = AsyncMock()
    market = MagicMock()
    svc = PortfolioService(repo, market)
    p = Portfolio(
        name="CEO",
        strategy=StrategyType.GROWTH,
        initial_capital=21.76,
        cash=15.0,
        positions=[
            PortfolioPosition(ticker="SNAP", shares=1, average_cost=8.0, current_price=6.0)
        ],
    )
    metrics = await svc.compute_metrics(p)
    # total = 15 + 6 = 21.00 vs 21.76 → −3.49%
    assert metrics["max_drawdown"] == -3.49


@pytest.mark.asyncio
async def test_ensure_no_base_creates_empty_not_twenty():
    svc = MagicMock(spec=PortfolioService)
    created = Portfolio(
        name="Portafolio CEO",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.REAL,
        initial_capital=0.0,
        cash=0.0,
    )
    svc.list_all = AsyncMock(return_value=[])
    svc.create = AsyncMock(return_value=created)
    alpaca = MagicMock()
    alpaca.is_configured.return_value = False
    deposited = MagicMock(amount=None, source="unavailable")
    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=deposited),
    ):
        p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "default"
    assert p.initial_capital == 0.0
    assert p.cash == 0.0
    assert svc.create.await_args.kwargs["initial_capital"] == 0.0
    assert svc.create.await_args.kwargs["cash"] == 0.0


@pytest.mark.asyncio
async def test_stamp_skips_conservative_equity():
    existing = Portfolio(
        name="CEO",
        strategy=StrategyType.GROWTH,
        mode=PortfolioMode.REAL,
        initial_capital=20.0,
        cash=15.87,
    )
    svc = MagicMock(spec=PortfolioService)
    svc.list_all = AsyncMock(return_value=[existing])
    svc.mirror_positions = AsyncMock()
    alpaca = MagicMock()
    alpaca.is_configured.return_value = True
    deposited = MagicMock(amount=21.01, source="conservative:equity")
    with patch(
        "services.portfolio_bootstrap_service.resolve_trading_base",
        AsyncMock(return_value=deposited),
    ):
        p, source = await PortfolioBootstrapService(svc, alpaca).ensure_portfolio()
    assert source == "existing"
    assert p.initial_capital == 20.0
    svc.mirror_positions.assert_not_called()
