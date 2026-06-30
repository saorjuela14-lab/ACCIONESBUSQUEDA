"""Watchlist repository."""

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WatchlistORM
from domain.entities import WatchlistItem


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, ticker: str, company_name: str | None = None, notes: str | None = None) -> WatchlistItem:
        item = WatchlistItem(ticker=ticker.upper(), company_name=company_name, notes=notes)
        orm = WatchlistORM(
            id=item.id,
            ticker=item.ticker,
            company_name=item.company_name,
            notes=item.notes,
            active=item.active,
            added_at=item.added_at,
        )
        self._session.add(orm)
        await self._session.commit()
        return item

    async def list_active(self) -> list[WatchlistItem]:
        result = await self._session.execute(
            select(WatchlistORM).where(WatchlistORM.active.is_(True))
        )
        return [
            WatchlistItem(
                id=r.id,
                ticker=r.ticker,
                company_name=r.company_name,
                notes=r.notes,
                added_at=r.added_at,
                active=r.active,
            )
            for r in result.scalars().all()
        ]

    async def remove(self, ticker: str) -> bool:
        result = await self._session.execute(
            select(WatchlistORM).where(WatchlistORM.ticker == ticker.upper())
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        row.active = False
        await self._session.commit()
        return True
