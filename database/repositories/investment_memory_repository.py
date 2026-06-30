"""Investment memory repository."""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AgentWeightORM, InvestmentMemoryORM
from domain.entities import InvestmentMemoryRecord


class InvestmentMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, record: InvestmentMemoryRecord) -> InvestmentMemoryRecord:
        orm = InvestmentMemoryORM(
            id=record.id,
            ticker=record.ticker,
            thesis=record.thesis,
            reasons_json=json.dumps(record.reasons),
            scores_json=json.dumps(record.scores),
            confidence=record.confidence,
            scenario=record.scenario,
            expected_outcome=record.expected_outcome,
            recommendation=record.recommendation,
            entry_price=record.entry_price,
            created_at=record.created_at,
        )
        self._session.add(orm)
        await self._session.commit()
        return record

    async def list_recent(self, limit: int = 10) -> list[InvestmentMemoryRecord]:
        result = await self._session.execute(
            select(InvestmentMemoryORM).order_by(InvestmentMemoryORM.created_at.desc()).limit(limit)
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def latest_by_ticker(self, tickers: list[str] | None = None) -> dict[str, InvestmentMemoryRecord]:
        """Most recent memory record per ticker (for watchlist matrix)."""
        result = await self._session.execute(
            select(InvestmentMemoryORM).order_by(InvestmentMemoryORM.created_at.desc())
        )
        wanted = {t.upper() for t in tickers} if tickers else None
        latest: dict[str, InvestmentMemoryRecord] = {}
        for row in result.scalars().all():
            t = row.ticker.upper()
            if t in latest:
                continue
            if wanted is not None and t not in wanted:
                continue
            latest[t] = self._to_domain(row)
            if wanted is not None and len(latest) >= len(wanted):
                break
        return latest

    async def list_ready_for_evaluation(self, min_age_days: int) -> list[InvestmentMemoryRecord]:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
        result = await self._session.execute(
            select(InvestmentMemoryORM).where(
                InvestmentMemoryORM.evaluated_at.is_(None),
                InvestmentMemoryORM.created_at <= cutoff,
            )
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def list_pending_evaluation(self) -> list[InvestmentMemoryRecord]:
        result = await self._session.execute(
            select(InvestmentMemoryORM).where(InvestmentMemoryORM.evaluated_at.is_(None))
        )
        return [self._to_domain(r) for r in result.scalars().all()]

    async def evaluate(self, record_id: str, was_correct: bool, notes: str, actual_return: float) -> None:
        result = await self._session.execute(
            select(InvestmentMemoryORM).where(InvestmentMemoryORM.id == record_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return
        row.evaluated_at = datetime.now(timezone.utc)
        row.was_correct = was_correct
        row.evaluation_notes = notes
        row.actual_return_pct = actual_return
        await self._session.commit()

    async def get_agent_weights(self) -> dict[str, float]:
        result = await self._session.execute(select(AgentWeightORM))
        weights = {r.agent_name: r.weight for r in result.scalars().all()}
        return weights or {}

    async def update_agent_weight(self, agent_name: str, weight: float, accuracy: float) -> None:
        result = await self._session.execute(
            select(AgentWeightORM).where(AgentWeightORM.agent_name == agent_name)
        )
        row = result.scalar_one_or_none()
        if row:
            row.weight = weight
            row.accuracy = accuracy
            row.updated_at = datetime.now(timezone.utc)
        else:
            self._session.add(AgentWeightORM(agent_name=agent_name, weight=weight, accuracy=accuracy))
        await self._session.commit()

    def _to_domain(self, row: InvestmentMemoryORM) -> InvestmentMemoryRecord:
        return InvestmentMemoryRecord(
            id=row.id,
            ticker=row.ticker,
            thesis=row.thesis,
            reasons=json.loads(row.reasons_json),
            scores=json.loads(row.scores_json),
            confidence=row.confidence,
            scenario=row.scenario,
            expected_outcome=row.expected_outcome,
            recommendation=row.recommendation,
            entry_price=row.entry_price,
            created_at=row.created_at,
            evaluated_at=row.evaluated_at,
            was_correct=row.was_correct,
            evaluation_notes=row.evaluation_notes,
            actual_return_pct=row.actual_return_pct,
        )
