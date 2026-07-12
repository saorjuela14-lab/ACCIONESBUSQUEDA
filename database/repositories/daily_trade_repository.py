"""Daily trade recommendation persistence."""

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DailyTradeReportORM
from domain.daily_trade import DailyTradeReport


class DailyTradeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, report: DailyTradeReport) -> str:
        report_id = str(uuid4())
        self._session.add(
            DailyTradeReportORM(
                id=report_id,
                report_date=report.report_date.isoformat(),
                session=report.session,
                report_json=report.model_dump_json(),
                created_at=report.generated_at,
            )
        )
        await self._session.commit()
        return report_id

    async def get_latest(self) -> DailyTradeReport | None:
        result = await self._session.execute(
            select(DailyTradeReportORM).order_by(DailyTradeReportORM.created_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return DailyTradeReport.model_validate_json(row.report_json)

    async def get_by_date(self, report_date: str) -> DailyTradeReport | None:
        result = await self._session.execute(
            select(DailyTradeReportORM)
            .where(DailyTradeReportORM.report_date == report_date)
            .order_by(DailyTradeReportORM.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return DailyTradeReport.model_validate_json(row.report_json)

    async def list_recent(self, limit: int = 7) -> list[DailyTradeReport]:
        result = await self._session.execute(
            select(DailyTradeReportORM)
            .order_by(DailyTradeReportORM.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [DailyTradeReport.model_validate_json(r.report_json) for r in rows]
