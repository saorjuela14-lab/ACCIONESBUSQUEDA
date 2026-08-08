"""Watchlist repository (org-scoped)."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WatchlistORM
from domain.entities import WatchlistItem


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_item(self, r: WatchlistORM) -> WatchlistItem:
        return WatchlistItem(
            id=r.id,
            org_id=getattr(r, "org_id", None),
            ticker=r.ticker,
            company_name=r.company_name,
            notes=r.notes,
            added_at=r.added_at,
            active=r.active,
        )

    async def add(
        self,
        ticker: str,
        company_name: str | None = None,
        notes: str | None = None,
        org_id: str | None = None,
    ) -> WatchlistItem:
        ticker_u = ticker.upper()
        # Reactivate existing row for same org+ticker if present
        q = select(WatchlistORM).where(WatchlistORM.ticker == ticker_u)
        if org_id is not None:
            q = q.where(WatchlistORM.org_id == org_id)
        else:
            q = q.where(or_(WatchlistORM.org_id.is_(None), WatchlistORM.org_id == "monarch"))
        result = await self._session.execute(q)
        row = result.scalar_one_or_none()
        if row:
            row.active = True
            row.notes = notes if notes is not None else row.notes
            row.company_name = company_name if company_name is not None else row.company_name
            if org_id and not row.org_id:
                row.org_id = org_id
            await self._session.commit()
            return self._to_item(row)

        item = WatchlistItem(
            ticker=ticker_u,
            company_name=company_name,
            notes=notes,
            org_id=org_id,
        )
        orm = WatchlistORM(
            id=item.id,
            org_id=org_id,
            ticker=item.ticker,
            company_name=item.company_name,
            notes=item.notes,
            active=item.active,
            added_at=item.added_at,
        )
        self._session.add(orm)
        await self._session.commit()
        return item

    async def list_active(self, org_id: str | None = None) -> list[WatchlistItem]:
        q = select(WatchlistORM).where(WatchlistORM.active.is_(True))
        if org_id is not None:
            q = q.where(WatchlistORM.org_id == org_id)
        result = await self._session.execute(q)
        return [self._to_item(r) for r in result.scalars().all()]

    async def remove(self, ticker: str, org_id: str | None = None) -> bool:
        q = select(WatchlistORM).where(WatchlistORM.ticker == ticker.upper())
        if org_id is not None:
            q = q.where(WatchlistORM.org_id == org_id)
        result = await self._session.execute(q)
        row = result.scalar_one_or_none()
        if not row:
            return False
        row.active = False
        await self._session.commit()
        return True
