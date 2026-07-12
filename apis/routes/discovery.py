"""Company discovery API — social media + news research."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.discovery import DiscoveryAnalyzeResult, DiscoveryProposalResult, DiscoveryReport
from models.schemas import DiscoveryAnalyzeRequest, DiscoveryResearchRequest, DiscoverProposalRequest
from providers.macro.factory import get_macro_provider
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from services.analysis_service import AnalysisService
from services.company_discovery_service import CompanyDiscoveryService
from services.discovery_proposal_service import DiscoveryProposalService
from services.investment_proposal_service import InvestmentProposalService
from services.llm_narrative_service import LLMNarrativeService
from services.watchlist_service import WatchlistService

router = APIRouter()


def _build_discovery_service(session: AsyncSession, with_analysis: bool = False) -> CompanyDiscoveryService:
    from config.settings import get_settings

    settings = get_settings()
    analysis = None
    if with_analysis:
        analysis = AnalysisService(
            market_provider=get_market_provider(),
            news_provider=get_news_provider(),
            macro_provider=get_macro_provider(),
            alert_repo=AlertRepository(session),
            memory_repo=InvestmentMemoryRepository(session),
            max_concentration_pct=settings.max_concentration_pct,
        )
    return CompanyDiscoveryService(
        market_provider=get_market_provider(),
        analysis_service=analysis,
    )


@router.post("/discover/research", response_model=DiscoveryReport)
async def discover_research(
    request: DiscoveryResearchRequest,
    session: AsyncSession = Depends(get_session),
) -> DiscoveryReport:
    """Investiga X, Reddit, StockTwits y noticias para descubrir empresas."""
    watchlist = await WatchlistRepository(session).list_active()
    exclude = list(request.exclude_tickers or [])
    exclude.extend(w.ticker for w in watchlist)

    service = _build_discovery_service(session)
    return await service.research(
        themes=request.themes,
        max_candidates=request.max_candidates,
        exclude_tickers=exclude,
    )


@router.post("/discover/analyze", response_model=DiscoveryAnalyzeResult)
async def discover_analyze(
    request: DiscoveryAnalyzeRequest,
    session: AsyncSession = Depends(get_session),
) -> DiscoveryAnalyzeResult:
    """Descubre empresas y analiza las mejores con el comité de inversión."""
    watchlist = await WatchlistRepository(session).list_active()
    exclude = list(request.exclude_tickers or [])
    exclude.extend(w.ticker for w in watchlist)

    portfolio = None
    if request.portfolio_id:
        portfolios = await PortfolioRepository(session).list_all()
        portfolio = next((p for p in portfolios if p.id == request.portfolio_id), None)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portafolio no encontrado")

    service = _build_discovery_service(session, with_analysis=True)
    return await service.research_and_analyze(
        themes=request.themes,
        max_candidates=request.max_candidates,
        analyze_top=request.analyze_top,
        exclude_tickers=exclude,
        portfolio=portfolio,
        watchlist=watchlist,
    )


@router.post("/discover/proposal", response_model=DiscoveryProposalResult)
async def discover_to_proposal(
    request: DiscoverProposalRequest,
    session: AsyncSession = Depends(get_session),
) -> DiscoveryProposalResult:
    """Descubre empresas, analiza las mejores y genera propuesta de inversión."""
    watchlist = await WatchlistRepository(session).list_active()
    exclude = [w.ticker for w in watchlist]

    portfolio = None
    if request.portfolio_id:
        portfolios = await PortfolioRepository(session).list_all()
        portfolio = next((p for p in portfolios if p.id == request.portfolio_id), None)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portafolio no encontrado")

    from config.settings import get_settings

    settings = get_settings()
    market = get_market_provider()
    analysis = AnalysisService(
        market_provider=market,
        news_provider=get_news_provider(),
        macro_provider=get_macro_provider(),
        alert_repo=AlertRepository(session),
        memory_repo=InvestmentMemoryRepository(session),
        max_concentration_pct=settings.max_concentration_pct,
    )
    svc = DiscoveryProposalService(
        discovery_service=CompanyDiscoveryService(market_provider=market),
        analysis_service=analysis,
        proposal_service=InvestmentProposalService(market),
        watchlist_service=WatchlistService(WatchlistRepository(session), market),
    )

    try:
        result = await svc.discover_and_propose(
            budget=request.budget,
            themes=request.themes,
            max_candidates=request.max_candidates,
            proposal_top=request.proposal_top,
            exclude_tickers=exclude,
            watchlist=watchlist,
            portfolio=portfolio,
            risk_profile=request.risk_profile,
            instrument_mode=request.instrument_mode,
            add_to_watchlist=request.add_to_watchlist,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.use_llm_narrative and result.proposal.executive_report:
        llm = LLMNarrativeService()
        text = await llm.enrich_proposal_report(result.proposal)
        if text:
            result.proposal.executive_report = llm.apply_to_executive_report(
                result.proposal.executive_report, text
            )

    return result
