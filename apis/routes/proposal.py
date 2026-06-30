"""Investment proposal API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.routes.analysis import _build_analysis_service
from database.engine import get_session
from database.repositories.watchlist_repository import WatchlistRepository
from domain.proposal import InvestmentProposal
from domain.proposal import InstrumentType, RiskProfile
from models.schemas import InvestmentProposalRequest
from providers.market.factory import get_market_provider
from services.investment_proposal_service import InvestmentProposalService

router = APIRouter()


@router.post("/proposal", response_model=InvestmentProposal)
async def create_proposal(
    request: InvestmentProposalRequest,
    session: AsyncSession = Depends(get_session),
) -> InvestmentProposal:
    tickers = [t.upper() for t in (request.tickers or [])]

    if not tickers and request.use_watchlist:
        watchlist = await WatchlistRepository(session).list_active()
        tickers = [w.ticker.upper() for w in watchlist]

    if not tickers:
        raise HTTPException(
            status_code=400,
            detail="Provide tickers or set use_watchlist=true with items in watchlist",
        )

    analysis = _build_analysis_service(session)
    theses = []
    for ticker in tickers:
        thesis = await analysis.analyze_ticker(ticker)
        theses.append(thesis)

    proposal_svc = InvestmentProposalService(get_market_provider())
    return await proposal_svc.build_proposal(
        budget=request.budget,
        theses=theses,
        instrument_mode=InstrumentType(request.instrument_mode),
        risk_profile=RiskProfile(request.risk_profile),
        cfd_margin_pct=request.cfd_margin_pct,
        tickers_filter=tickers,
    )
