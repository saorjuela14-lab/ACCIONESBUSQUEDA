"""Alpaca broker status and order execution API."""

from fastapi import APIRouter, HTTPException, Query

from domain.broker import (
    BrokerAccount,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
    BrokerStatus,
    ExecuteOrdersRequest,
    ExecuteOrdersResponse,
)
from models.schemas import MicroPlanExecuteRequest, TradePickExecuteRequest
from services.alpaca_order_service import AlpacaOrderService

router = APIRouter()


def _svc() -> AlpacaOrderService:
    return AlpacaOrderService()


@router.get("/broker/status", response_model=BrokerStatus)
async def broker_status() -> BrokerStatus:
    """Estado de conexión Alpaca (paper/live) y resumen de cuenta."""
    return await _svc().status()


@router.get("/broker/account", response_model=BrokerAccount)
async def broker_account() -> BrokerAccount:
    svc = _svc()
    if not svc.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")
    try:
        return await svc.get_account()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/positions", response_model=list[BrokerPosition])
async def broker_positions() -> list[BrokerPosition]:
    svc = _svc()
    if not svc.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")
    try:
        return await svc.get_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/orders", response_model=list[BrokerOrderResult])
async def broker_orders(
    status: str = Query(default="open", description="open | closed | all"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[BrokerOrderResult]:
    svc = _svc()
    if not svc.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")
    try:
        return await svc.list_orders(status=status, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/broker/orders", response_model=BrokerOrderResult)
async def submit_broker_order(request: BrokerOrderRequest) -> BrokerOrderResult:
    """Envía una orden individual a Alpaca (market/limit; bracket si hay stop+target)."""
    svc = _svc()
    if not svc.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")
    if not svc.paper:
        raise HTTPException(
            status_code=400,
            detail="Usa POST /broker/execute con confirm_live=true para cuenta LIVE",
        )
    result = await svc.submit_one(request)
    if result.error:
        raise HTTPException(status_code=502, detail=result.error)
    return result


@router.post("/broker/execute", response_model=ExecuteOrdersResponse)
async def execute_orders(request: ExecuteOrdersRequest) -> ExecuteOrdersResponse:
    """Ejecuta varias líneas (p.ej. plan micro). dry_run=true no envía a Alpaca."""
    return await _svc().execute(request)


@router.post("/broker/execute/micro-plan", response_model=ExecuteOrdersResponse)
async def execute_micro_plan(request: MicroPlanExecuteRequest) -> ExecuteOrdersResponse:
    """Ejecuta el plan de 'Gestionar capital' en Alpaca (acciones enteras)."""
    svc = _svc()
    lines = svc.lines_from_micro_plan(request.lines)
    if not lines:
        return ExecuteOrdersResponse(
            paper=svc.paper,
            dry_run=request.dry_run,
            warnings=["El plan no tiene líneas con shares > 0"],
        )
    return await svc.execute(
        ExecuteOrdersRequest(
            lines=lines,
            dry_run=request.dry_run,
            confirm_live=request.confirm_live,
            sync_portfolio_id=request.sync_portfolio_id,
        )
    )


@router.post("/broker/execute/pick", response_model=ExecuteOrdersResponse)
async def execute_trade_pick(request: TradePickExecuteRequest) -> ExecuteOrdersResponse:
    """Compra una recomendación de corto plazo (1+ acciones enteras)."""
    from domain.broker import ExecuteLine

    shares = max(1, int(request.shares))
    line = ExecuteLine(
        ticker=request.ticker.upper(),
        shares=float(shares),
        side="buy",
        order_type="market",
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
    )
    return await _svc().execute(
        ExecuteOrdersRequest(
            lines=[line],
            dry_run=request.dry_run,
            confirm_live=request.confirm_live,
        )
    )
