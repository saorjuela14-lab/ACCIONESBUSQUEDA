"""Report API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.report_repository import ReportRepository
from domain.reports import DailyInvestmentReport, MarketReport

router = APIRouter()


@router.get("/reports/market/latest", response_model=MarketReport)
async def latest_market_report(
    session: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> MarketReport:
    report = await ReportRepository(db).get_latest_market_report(session)
    if not report:
        raise HTTPException(status_code=404, detail="No market reports yet")
    return report


@router.get("/reports/daily/latest", response_model=DailyInvestmentReport)
async def latest_daily_report(db: AsyncSession = Depends(get_session)) -> DailyInvestmentReport:
    report = await ReportRepository(db).get_latest_daily_report()
    if not report:
        raise HTTPException(status_code=404, detail="No daily reports yet")
    return report
