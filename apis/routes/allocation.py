"""Market allocation advisor API."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.allocation_plan import MarketAllocationPlan
from models.schemas import AllocationAdviseRequest
from providers.market.factory import get_market_provider
from services.market_allocation_advisor_service import MarketAllocationAdvisorService
from services.market_dashboard_service import MarketDashboardService

router = APIRouter()


@router.post("/allocation/advise", response_model=MarketAllocationPlan)
async def allocation_advise(
    request: AllocationAdviseRequest,
    session: AsyncSession = Depends(get_session),
) -> MarketAllocationPlan:
    watchlist = await WatchlistRepository(session).list_active()
    if not watchlist:
        raise HTTPException(status_code=400, detail="La watchlist está vacía. Agrega tickers primero.")

    memory = await InvestmentMemoryRepository(session).latest_by_ticker(
        [w.ticker for w in watchlist]
    )

    dash = MarketDashboardService()
    indices, sectors, _, _ = await asyncio.gather(
        dash._fetch_indices(),
        dash._fetch_sector_heatmap(),
        dash._economic_calendar(),
        dash._market_news(),
    )
    regime, regime_score = dash._compute_market_regime(indices, sectors)
    strong_sectors = [s.sector for s in sectors if s.regime == "bullish"][:5]

    # Overlay macro risk mode (risk_off / crisis) on price regime for bucket weights
    try:
        from services.macro_regime_service import MacroRegimeService

        macro = await MacroRegimeService().assess(market_regime=regime)
        if macro.mode in ("risk_off", "crisis", "risk_on"):
            regime = macro.mode
            regime_score = macro.score
    except Exception:
        pass

    advisor = MarketAllocationAdvisorService(get_market_provider())
    return await advisor.advise(
        capital=request.capital,
        watchlist=watchlist,
        memory_by_ticker=memory,
        market_regime=regime,
        market_regime_score=regime_score,
        strategy_style=request.strategy_style,
        strong_sectors=strong_sectors,
    )
