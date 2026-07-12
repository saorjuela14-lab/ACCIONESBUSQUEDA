"""Portfolio API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.portfolio_snapshot_repository import PortfolioSnapshotRepository
from domain.dashboard import PortfolioHistoryPoint
from domain.entities import Portfolio
from domain.enums import PortfolioMode, StrategyType
from domain.portfolio_demo import PortfolioProjectionReport
from models.schemas import DemoSimulateRequest, PortfolioCreateRequest, PositionAddRequest
from providers.market.factory import get_market_provider
from services.demo_projection_service import DemoProjectionService
from services.portfolio_service import PortfolioService

router = APIRouter()


def _build_service(session: AsyncSession) -> PortfolioService:
    return PortfolioService(PortfolioRepository(session), get_market_provider())


@router.get("/portfolios", response_model=list[Portfolio])
async def list_portfolios(session: AsyncSession = Depends(get_session)) -> list[Portfolio]:
    return await _build_service(session).list_all()


@router.post("/portfolios", response_model=Portfolio)
async def create_portfolio(
    request: PortfolioCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> Portfolio:
    return await _build_service(session).create(
        name=request.name,
        strategy=request.strategy,
        initial_capital=request.initial_capital,
        cash=request.cash,
        mode=request.mode,
    )


@router.post("/portfolios/default", response_model=Portfolio)
async def create_default_portfolio(
    request: PortfolioCreateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> Portfolio:
    """Create a portfolio with optional mode and capital (defaults: real, $1000)."""
    service = _build_service(session)
    if request:
        return await service.create(
            name=request.name,
            strategy=request.strategy,
            initial_capital=request.initial_capital,
            cash=request.cash,
            mode=request.mode,
        )
    existing = await service.list_all()
    if existing:
        return existing[0]
    return await service.create(
        name="Portafolio CEO",
        strategy=StrategyType.GROWTH,
        initial_capital=1000.0,
        cash=1000.0,
        mode=PortfolioMode.REAL,
    )


@router.get("/portfolios/{portfolio_id}/projections", response_model=PortfolioProjectionReport)
async def portfolio_projections(
    portfolio_id: str,
    horizon_months: int = 12,
    session: AsyncSession = Depends(get_session),
) -> PortfolioProjectionReport:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    if portfolio.mode != PortfolioMode.DEMO:
        raise HTTPException(status_code=400, detail="Proyecciones disponibles solo en portafolios demo")
    portfolio = await service.refresh_prices(portfolio_id)
    proj = DemoProjectionService(get_market_provider())
    return await proj.project(portfolio, horizon_months=horizon_months)


@router.post("/portfolios/{portfolio_id}/simulate", response_model=PortfolioProjectionReport)
async def portfolio_simulate(
    portfolio_id: str,
    request: DemoSimulateRequest,
    session: AsyncSession = Depends(get_session),
) -> PortfolioProjectionReport:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    if portfolio.mode != PortfolioMode.DEMO:
        raise HTTPException(status_code=400, detail="Simulaciones disponibles solo en portafolios demo")
    portfolio = await service.refresh_prices(portfolio_id)
    proj = DemoProjectionService(get_market_provider())
    budget = request.proposal_budget or portfolio.cash
    return await proj.simulate_proposal_impact(
        portfolio,
        proposal_budget=budget,
        expected_return_pct=request.expected_return_pct,
        horizon_months=request.horizon_months,
    )


@router.post("/portfolios/{portfolio_id}/positions", response_model=Portfolio)
async def add_position(
    portfolio_id: str,
    request: PositionAddRequest,
    session: AsyncSession = Depends(get_session),
) -> Portfolio:
    try:
        return await _build_service(session).add_position(
            portfolio_id, request.ticker, request.shares, request.average_cost
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/metrics")
async def portfolio_metrics(
    portfolio_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    portfolio = await service.refresh_prices(portfolio_id)
    return await service.compute_metrics(portfolio)


@router.get("/portfolios/{portfolio_id}/history", response_model=list[PortfolioHistoryPoint])
async def portfolio_history(
    portfolio_id: str,
    limit: int = 120,
    session: AsyncSession = Depends(get_session),
) -> list[PortfolioHistoryPoint]:
    return await PortfolioSnapshotRepository(session).list_for_portfolio(portfolio_id, limit=limit)
