"""Watchlist snapshot repository."""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import WatchlistSnapshotORM


class WatchlistSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, ticker: str) -> dict | None:
        result = await self._session.execute(
            select(WatchlistSnapshotORM).where(WatchlistSnapshotORM.ticker == ticker.upper())
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return json.loads(row.snapshot_json)

    async def save(self, ticker: str, snapshot: dict) -> None:
        ticker = ticker.upper()
        result = await self._session.execute(
            select(WatchlistSnapshotORM).where(WatchlistSnapshotORM.ticker == ticker)
        )
        row = result.scalar_one_or_none()
        data = json.dumps(snapshot, default=str)
        now = datetime.now(timezone.utc)
        if row:
            row.snapshot_json = data
            row.updated_at = now
        else:
            self._session.add(WatchlistSnapshotORM(ticker=ticker, snapshot_json=data, updated_at=now))
        await self._session.commit()
