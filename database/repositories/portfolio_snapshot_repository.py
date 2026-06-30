"""Portfolio value history snapshots."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PortfolioSnapshotORM
from domain.dashboard import PortfolioHistoryPoint


class PortfolioSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, portfolio_id: str, total_value: float, return_pct: float, cash: float) -> None:
        self._session.add(
            PortfolioSnapshotORM(
                id=str(uuid4()),
                portfolio_id=portfolio_id,
                total_value=total_value,
                return_pct=return_pct,
                cash=cash,
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()

    async def list_for_portfolio(self, portfolio_id: str, limit: int = 120) -> list[PortfolioHistoryPoint]:
        result = await self._session.execute(
            select(PortfolioSnapshotORM)
            .where(PortfolioSnapshotORM.portfolio_id == portfolio_id)
            .order_by(PortfolioSnapshotORM.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return [
            PortfolioHistoryPoint(
                timestamp=r.created_at,
                total_value=r.total_value,
                return_pct=r.return_pct,
                cash=r.cash,
            )
            for r in rows
        ]
