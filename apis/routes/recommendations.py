"""Daily short-term trade recommendation API."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.daily_trade import DailyTradeReport
from models.schemas import DailyTradeGenerateRequest
from providers.market.factory import get_market_provider
from services.company_discovery_service import CompanyDiscoveryService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService

router = APIRouter()


def _build_service(session: AsyncSession) -> DailyTradeRecommendationService:
    market = get_market_provider()
    return DailyTradeRecommendationService(
        market_provider=market,
        discovery_service=CompanyDiscoveryService(market_provider=market),
        trade_repo=DailyTradeRepository(session),
    )


@router.get("/recommendations/daily/latest", response_model=DailyTradeReport)
async def latest_daily_trades(
    session: AsyncSession = Depends(get_session),
) -> DailyTradeReport:
    """Últimas recomendaciones diarias de corto plazo."""
    report = await _build_service(session).get_latest()
    if not report:
        report = await _build_service(session).generate(session="pre_market", persist=True)
    return report


@router.post("/recommendations/daily/generate", response_model=DailyTradeReport)
async def generate_daily_trades(
    request: DailyTradeGenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> DailyTradeReport:
    """Genera recomendaciones de corto plazo bajo demanda."""
    watchlist = await WatchlistRepository(session).list_active()
    exclude = list(request.exclude_tickers or [])
    exclude.extend(w.ticker for w in watchlist)

    return await _build_service(session).generate(
        session=request.session,
        max_picks=request.max_picks,
        exclude_tickers=exclude,
        persist=True,
        capital=request.capital,
    )


@router.get("/recommendations/daily/history", response_model=list[DailyTradeReport])
async def daily_trades_history(
    limit: int = 7,
    session: AsyncSession = Depends(get_session),
) -> list[DailyTradeReport]:
    """Historial reciente de recomendaciones diarias."""
    repo = DailyTradeRepository(session)
    reports = await repo.list_recent(limit=min(limit, 30))
    return reports
