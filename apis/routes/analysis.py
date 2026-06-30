"""Analysis API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.reports import InvestmentThesis
from models.schemas import AnalyzeRequest
from providers.macro.factory import get_macro_provider
from providers.market.factory import get_market_provider
from providers.sentiment.factory import get_sentiment_provider
from providers.news.factory import get_news_provider
from reports.writer import ReportWriter
from services.analysis_service import AnalysisService

router = APIRouter()


def _build_analysis_service(session: AsyncSession) -> AnalysisService:
    from config.settings import get_settings

    settings = get_settings()
    return AnalysisService(
        market_provider=get_market_provider(),
        news_provider=get_news_provider(),
        macro_provider=get_macro_provider(),
        alert_repo=AlertRepository(session),
        memory_repo=InvestmentMemoryRepository(session),
        sentiment_provider=get_sentiment_provider(),
        max_concentration_pct=settings.max_concentration_pct,
    )


@router.post("/analyze", response_model=InvestmentThesis)
async def analyze_ticker(
    request: AnalyzeRequest,
    session: AsyncSession = Depends(get_session),
) -> InvestmentThesis:
    service = _build_analysis_service(session)
    portfolio = None
    watchlist = await WatchlistRepository(session).list_active()

    if request.portfolio_id:
        portfolios = await PortfolioRepository(session).list_all()
        portfolio = next((p for p in portfolios if p.id == request.portfolio_id), None)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

    thesis = await service.analyze_ticker(request.ticker, portfolio=portfolio, watchlist=watchlist)
    ReportWriter().write_thesis(thesis)
    return thesis


@router.get("/analyze/{ticker}", response_model=InvestmentThesis)
async def analyze_ticker_get(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> InvestmentThesis:
    return await analyze_ticker(AnalyzeRequest(ticker=ticker), session)
