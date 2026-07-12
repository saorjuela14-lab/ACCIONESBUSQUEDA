"""Portfolio repository."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PortfolioORM
from domain.entities import Portfolio, PortfolioPosition
from domain.enums import PortfolioMode, StrategyType


class PortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, row: PortfolioORM) -> Portfolio:
        positions = [PortfolioPosition(**p) for p in json.loads(row.positions_json)]
        mode_raw = getattr(row, "mode", None) or "real"
        try:
            mode = PortfolioMode(mode_raw)
        except ValueError:
            mode = PortfolioMode.REAL
        return Portfolio(
            id=row.id,
            name=row.name,
            strategy=StrategyType(row.strategy),
            mode=mode,
            initial_capital=row.initial_capital,
            cash=row.cash,
            positions=positions,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, portfolio: Portfolio) -> Portfolio:
        orm = PortfolioORM(
            id=portfolio.id,
            name=portfolio.name,
            strategy=portfolio.strategy.value,
            mode=portfolio.mode.value,
            initial_capital=portfolio.initial_capital,
            cash=portfolio.cash,
            positions_json=json.dumps([p.model_dump() for p in portfolio.positions]),
            created_at=portfolio.created_at,
            updated_at=portfolio.updated_at,
        )
        self._session.add(orm)
        await self._session.commit()
        return portfolio

    async def list_all(self) -> list[Portfolio]:
        result = await self._session.execute(select(PortfolioORM))
        return [self._to_entity(row) for row in result.scalars().all()]

    async def get_by_id(self, portfolio_id: str) -> Portfolio | None:
        result = await self._session.execute(
            select(PortfolioORM).where(PortfolioORM.id == portfolio_id)
        )
        row = result.scalar_one_or_none()
        return self._to_entity(row) if row else None

    async def update(self, portfolio: Portfolio) -> Portfolio:
        result = await self._session.execute(
            select(PortfolioORM).where(PortfolioORM.id == portfolio.id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise ValueError(f"Portfolio {portfolio.id} not found")
        row.cash = portfolio.cash
        row.positions_json = json.dumps([p.model_dump() for p in portfolio.positions])
        row.updated_at = portfolio.updated_at
        await self._session.commit()
        return portfolio
