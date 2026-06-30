"""Report API routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
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


@router.get("/reports/daily/latest/export")
async def export_daily_briefing(db: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    report = await ReportRepository(db).get_latest_daily_report()
    if not report:
        raise HTTPException(status_code=404, detail="No daily reports yet")
    mr = report.market_report
    lines = [
        f"# NexBuy CEO Briefing — {report.date.date() if hasattr(report.date, 'date') else report.date}",
        "",
        "## Market Summary",
        mr.market_summary,
        "",
        f"**Strong sectors:** {', '.join(mr.strong_sectors) or '—'}",
        f"**Weak sectors:** {', '.join(mr.weak_sectors) or '—'}",
        "",
        "## Opportunities",
        ", ".join(report.top_opportunities) or "—",
        "",
        "## Worst Performers",
        ", ".join(report.worst_performers) or "—",
        "",
        "## Watchlist Changes",
        *[f"- {c}" for c in report.watchlist_changes],
        "",
        "## Alerts",
        *[f"- {a}" for a in report.alerts],
    ]
    body = "\n".join(lines)
    return PlainTextResponse(content=body, media_type="text/markdown; charset=utf-8")
