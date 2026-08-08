"""Portfolio API routes."""

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
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
async def list_portfolios(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> list[Portfolio]:
    return await _build_service(session).list_all(org_id=scope.read_org_id())


@router.post("/portfolios", response_model=Portfolio)
async def create_portfolio(
    request: PortfolioCreateRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> Portfolio:
    return await _build_service(session).create(
        name=request.name,
        strategy=request.strategy,
        initial_capital=request.initial_capital,
        cash=request.cash,
        mode=request.mode,
        org_id=scope.write_org_id(),
    )


@router.post("/portfolios/default", response_model=Portfolio)
async def create_default_portfolio(
    request: PortfolioCreateRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> Portfolio:
    """Return existing book portfolio, or create a default one for this org."""
    service = _build_service(session)
    org = scope.write_org_id()
    existing = await service.list_all(org_id=org)
    if existing:
        return sorted(existing, key=lambda x: x.updated_at, reverse=True)[0]
    if request is not None:
        return await service.create(
            name=request.name,
            strategy=request.strategy,
            initial_capital=request.initial_capital,
            cash=request.cash,
            mode=request.mode,
            org_id=org,
        )
    scope.require_desk()
    return await service.create(
        name="Portafolio CEO",
        strategy=StrategyType.GROWTH,
        initial_capital=20.0,
        cash=20.0,
        mode=PortfolioMode.REAL,
        org_id=org,
    )


@router.post("/portfolios/sync-alpaca", response_model=Portfolio)
async def sync_portfolio_from_alpaca(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> Portfolio:
    """Recrea/actualiza el portafolio NexBuy desde la cuenta Alpaca (tras redeploy)."""
    if not scope.is_desk:
        raise HTTPException(status_code=403, detail="Solo la mesa puede sincronizar Alpaca")
    from services.alpaca_order_service import AlpacaOrderService
    from services.portfolio_bootstrap_service import PortfolioBootstrapService

    svc = _build_service(session)
    alpaca = AlpacaOrderService()
    if not alpaca.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")
    boot = PortfolioBootstrapService(svc, alpaca)
    existing = await svc.list_all(org_id=scope.read_org_id())
    if existing:
        # Refresh cash/positions from Alpaca onto newest portfolio
        account = await alpaca.get_account()
        broker_positions = await alpaca.get_positions()
        from domain.entities import PortfolioPosition

        from domain.firm_capital import FIRM_RETURN_BASE_USD

        cash = float(account.cash or 0)
        positions = []
        for pos in broker_positions:
            qty = float(pos.qty or 0)
            if qty <= 0:
                continue
            avg = float(pos.avg_entry_price or pos.current_price or 0)
            if avg <= 0:
                continue
            positions.append(
                PortfolioPosition(
                    ticker=pos.symbol.upper(),
                    shares=qty,
                    average_cost=avg,
                    current_price=float(pos.current_price or avg),
                )
            )
        p = sorted(existing, key=lambda x: x.updated_at, reverse=True)[0]
        return await svc.mirror_positions(
            p.id,
            positions=positions,
            cash=round(cash, 2),
            # Keep $20 return base — do not overwrite with live Alpaca equity
            initial_capital=FIRM_RETURN_BASE_USD,
            org_id=scope.write_org_id(),
        )
    synced = await boot.sync_from_alpaca(org_id=scope.write_org_id())
    if not synced:
        raise HTTPException(status_code=502, detail="No se pudo sincronizar desde Alpaca")
    return synced


@router.get("/portfolios/{portfolio_id}/projections", response_model=PortfolioProjectionReport)
async def portfolio_projections(
    portfolio_id: str,
    horizon_months: int = 12,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> PortfolioProjectionReport:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id, org_id=scope.read_org_id())
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
    scope: OrgScope = Depends(get_org_scope),
) -> PortfolioProjectionReport:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id, org_id=scope.read_org_id())
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
    scope: OrgScope = Depends(get_org_scope),
) -> Portfolio:
    try:
        return await _build_service(session).add_position(
            portfolio_id,
            request.ticker,
            request.shares,
            request.average_cost,
            org_id=scope.read_org_id(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/metrics")
async def portfolio_metrics(
    portfolio_id: str,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    service = _build_service(session)
    portfolio = await service.get_by_id(portfolio_id, org_id=scope.read_org_id())
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    portfolio = await service.refresh_prices(portfolio_id)
    return await service.compute_metrics(portfolio)


@router.get("/portfolios/{portfolio_id}/history", response_model=list[PortfolioHistoryPoint])
async def portfolio_history(
    portfolio_id: str,
    limit: int = 120,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> list[PortfolioHistoryPoint]:
    # Ownership check
    p = await _build_service(session).get_by_id(portfolio_id, org_id=scope.read_org_id())
    if not p:
        raise HTTPException(status_code=404, detail="Portafolio no encontrado")
    return await PortfolioSnapshotRepository(session).list_for_portfolio(portfolio_id, limit=limit)
