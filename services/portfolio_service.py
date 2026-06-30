"""Portfolio management service."""

from datetime import datetime, timezone

from database.repositories.portfolio_repository import PortfolioRepository
from domain.entities import Portfolio, PortfolioPosition
from domain.enums import StrategyType
from providers.interfaces import MarketDataProvider


class PortfolioService:
    def __init__(self, repo: PortfolioRepository, market_provider: MarketDataProvider) -> None:
        self._repo = repo
        self._market = market_provider

    async def create(
        self, name: str, strategy: StrategyType, initial_capital: float, cash: float | None = None
    ) -> Portfolio:
        portfolio = Portfolio(
            name=name,
            strategy=strategy,
            initial_capital=initial_capital,
            cash=cash if cash is not None else initial_capital,
        )
        return await self._repo.create(portfolio)

    async def list_all(self) -> list[Portfolio]:
        return await self._repo.list_all()

    async def add_position(
        self, portfolio_id: str, ticker: str, shares: float, average_cost: float
    ) -> Portfolio:
        portfolios = await self._repo.list_all()
        portfolio = next((p for p in portfolios if p.id == portfolio_id), None)
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} not found")

        existing = next((p for p in portfolio.positions if p.ticker == ticker.upper()), None)
        if existing:
            total_shares = existing.shares + shares
            existing.average_cost = (
                (existing.average_cost * existing.shares + average_cost * shares) / total_shares
            )
            existing.shares = total_shares
        else:
            portfolio.positions.append(
                PortfolioPosition(ticker=ticker.upper(), shares=shares, average_cost=average_cost)
            )

        portfolio.cash -= shares * average_cost
        portfolio.updated_at = datetime.now(timezone.utc)
        return await self._repo.update(portfolio)

    async def refresh_prices(self, portfolio_id: str) -> Portfolio:
        portfolios = await self._repo.list_all()
        portfolio = next((p for p in portfolios if p.id == portfolio_id), None)
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} not found")

        for position in portfolio.positions:
            quote = await self._market.get_quote(position.ticker)
            position.current_price = float(quote.get("current_price") or position.average_cost)

        portfolio.updated_at = datetime.now(timezone.utc)
        return await self._repo.update(portfolio)

    async def compute_metrics(self, portfolio: Portfolio) -> dict[str, float | None]:
        """Compute portfolio risk metrics from available position data."""
        returns = []
        for position in portfolio.positions:
            if position.current_price and position.average_cost:
                ret = (position.current_price - position.average_cost) / position.average_cost
                returns.append(ret)

        if not returns:
            return {"sharpe": None, "sortino": None, "max_drawdown": None, "beta": None}

        import numpy as np

        arr = np.array(returns)
        mean_ret = float(arr.mean())
        std_ret = float(arr.std()) if arr.std() > 0 else 0.001
        downside = arr[arr < 0]
        downside_std = float(downside.std()) if len(downside) > 0 and downside.std() > 0 else 0.001

        return {
            "sharpe": round(mean_ret / std_ret * (252 ** 0.5), 2),
            "sortino": round(mean_ret / downside_std * (252 ** 0.5), 2),
            "max_drawdown": round(float(arr.min()) * 100, 2),
            "beta": None,
            "return_pct": round(portfolio.return_pct, 2),
        }
