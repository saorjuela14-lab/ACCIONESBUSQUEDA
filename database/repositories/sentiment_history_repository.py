"""Sentiment history persistence."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SentimentHistoryORM
from domain.sentiment_v2 import SentimentHistoryPoint


class SentimentHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        ticker: str,
        aggregated_score: float,
        label: str,
        retail_score: float = 0.0,
        news_score: float = 0.0,
        institutional_score: float = 0.0,
    ) -> None:
        self._session.add(
            SentimentHistoryORM(
                id=str(uuid4()),
                ticker=ticker.upper(),
                aggregated_score=aggregated_score,
                label=label,
                retail_score=retail_score,
                news_score=news_score,
                institutional_score=institutional_score,
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()

    async def list_for_ticker(self, ticker: str, limit: int = 90) -> list[SentimentHistoryPoint]:
        result = await self._session.execute(
            select(SentimentHistoryORM)
            .where(SentimentHistoryORM.ticker == ticker.upper())
            .order_by(SentimentHistoryORM.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return [
            SentimentHistoryPoint(
                timestamp=r.created_at,
                aggregated_score=r.aggregated_score,
                label=r.label,
                retail_score=r.retail_score,
                news_score=r.news_score,
                institutional_score=r.institutional_score,
            )
            for r in rows
        ]
