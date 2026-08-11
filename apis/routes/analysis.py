"""Analysis API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
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
from services.llm_narrative_service import LLMNarrativeService
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
    scope: OrgScope = Depends(get_org_scope),
) -> InvestmentThesis:
    service = _build_analysis_service(session)
    org = scope.book_org_id()
    portfolio = None
    watchlist = await WatchlistRepository(session).list_active(org_id=org)

    if request.portfolio_id:
        portfolio = await PortfolioRepository(session).get_by_id(
            request.portfolio_id, org_id=org
        )
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

    thesis = await service.analyze_ticker(request.ticker, portfolio=portfolio, watchlist=watchlist)
    llm = LLMNarrativeService()
    extra = await llm.enrich_thesis_summary(thesis)
    if extra:
        thesis.executive_summary = f"{extra}\n\n{thesis.executive_summary}"
    ReportWriter().write_thesis(thesis)
    return thesis


@router.get("/analyze/{ticker}", response_model=InvestmentThesis)
async def analyze_ticker_get(
    ticker: str,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> InvestmentThesis:
    return await analyze_ticker(AnalyzeRequest(ticker=ticker), session, scope)


@router.get("/research/pack/{ticker}")
async def research_pack(
    ticker: str,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
):
    """PASO 02 — one-screen research: why moving, flow proxies, news, HTF."""
    _ = session  # reserved for future caching
    from services.research_pack_service import ResearchPackService

    try:
        sentiment = get_sentiment_provider()
    except Exception:
        sentiment = None
    try:
        news = get_news_provider()
    except Exception:
        news = None
    pack = await ResearchPackService(
        market=get_market_provider(),
        news=news,
        sentiment_provider=sentiment,
    ).build(ticker)
    return pack
