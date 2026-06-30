"""Investment proposal API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.routes.analysis import _build_analysis_service
from database.engine import get_session
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.entities import Portfolio
from domain.proposal import InvestmentProposal
from domain.proposal import InstrumentType, RiskProfile
from models.schemas import InvestmentProposalRequest, ProposalApplyRequest
from providers.market.factory import get_market_provider
from services.investment_proposal_service import InvestmentProposalService
from services.llm_narrative_service import LLMNarrativeService
from services.proposal_apply_service import ProposalApplyService
from services.portfolio_service import PortfolioService

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
    proposal = await proposal_svc.build_proposal(
        budget=request.budget,
        theses=theses,
        instrument_mode=InstrumentType(request.instrument_mode),
        risk_profile=RiskProfile(request.risk_profile),
        cfd_margin_pct=request.cfd_margin_pct,
        tickers_filter=tickers,
    )

    if request.use_llm_narrative and proposal.executive_report:
        llm = LLMNarrativeService()
        text = await llm.enrich_proposal_report(proposal)
        if text:
            proposal.executive_report = llm.apply_to_executive_report(proposal.executive_report, text)

    return proposal


@router.post("/proposal/apply", response_model=Portfolio)
async def apply_proposal(
    request: ProposalApplyRequest,
    session: AsyncSession = Depends(get_session),
) -> Portfolio:
    svc = ProposalApplyService(PortfolioService(PortfolioRepository(session), get_market_provider()))
    try:
        portfolio, warnings = await svc.apply(request.portfolio_id, request.proposal)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if warnings:
        portfolio  # returned as-is; warnings could be in response header — keep simple
    return portfolio
