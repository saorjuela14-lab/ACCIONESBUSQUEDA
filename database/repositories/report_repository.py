"""Market and daily report repositories."""

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DailyReportORM, MarketReportORM
from domain.reports import DailyInvestmentReport, MarketReport


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_market_report(self, report: MarketReport) -> str:
        report_id = str(uuid4())
        self._session.add(
            MarketReportORM(
                id=report_id,
                session=report.session.value,
                report_json=report.model_dump_json(),
                created_at=report.timestamp,
            )
        )
        await self._session.commit()
        return report_id

    async def get_latest_market_report(self, session: str | None = None) -> MarketReport | None:
        query = select(MarketReportORM).order_by(MarketReportORM.created_at.desc()).limit(1)
        if session:
            query = (
                select(MarketReportORM)
                .where(MarketReportORM.session == session)
                .order_by(MarketReportORM.created_at.desc())
                .limit(1)
            )
        result = await self._session.execute(query)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return MarketReport.model_validate_json(row.report_json)

    async def save_daily_report(self, report: DailyInvestmentReport) -> str:
        report_id = str(uuid4())
        date_str = report.date.strftime("%Y-%m-%d")
        self._session.add(
            DailyReportORM(
                id=report_id,
                report_date=date_str,
                report_json=report.model_dump_json(),
                created_at=report.date,
            )
        )
        await self._session.commit()
        return report_id

    async def get_latest_daily_report(self) -> DailyInvestmentReport | None:
        result = await self._session.execute(
            select(DailyReportORM).order_by(DailyReportORM.created_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return DailyInvestmentReport.model_validate_json(row.report_json)
