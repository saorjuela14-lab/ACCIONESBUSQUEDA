"""Alpaca broker status and order execution API.

Inspired by https://github.com/alpacahq/cli command surface
(account, clock, doctor, order, position) — implemented as HTTP for NexBuy.
"""

from fastapi import APIRouter, HTTPException, Query

from domain.broker import (
    BrokerAccount,
    BrokerClock,
    BrokerDoctorReport,
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


def _require_configured(svc: AlpacaOrderService) -> None:
    if not svc.is_configured():
        raise HTTPException(status_code=503, detail="Alpaca no configurada")


def _require_live_confirm(svc: AlpacaOrderService, confirm_live: bool) -> None:
    if not svc.paper and not confirm_live:
        raise HTTPException(
            status_code=400,
            detail="Cuenta LIVE: pasa confirm_live=true (dinero real)",
        )


@router.get("/broker/status", response_model=BrokerStatus)
async def broker_status() -> BrokerStatus:
    """Estado de conexión Alpaca (paper/live), cuenta y clock."""
    return await _svc().status()


@router.get("/broker/doctor", response_model=BrokerDoctorReport)
async def broker_doctor() -> BrokerDoctorReport:
    """Diagnóstico de conectividad (equivalente a `alpaca doctor`)."""
    return await _svc().doctor()


@router.get("/broker/clock", response_model=BrokerClock)
async def broker_clock() -> BrokerClock:
    """Reloj de mercado US (`alpaca clock`)."""
    svc = _svc()
    _require_configured(svc)
    try:
        return await svc.get_clock()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/account", response_model=BrokerAccount)
async def broker_account() -> BrokerAccount:
    svc = _svc()
    _require_configured(svc)
    try:
        return await svc.get_account()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/positions", response_model=list[BrokerPosition])
async def broker_positions() -> list[BrokerPosition]:
    svc = _svc()
    _require_configured(svc)
    try:
        return await svc.get_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/broker/positions/{symbol}")
async def close_broker_position(
    symbol: str,
    confirm_live: bool = Query(default=False),
) -> dict:
    """Cierra una posición (`alpaca position close`)."""
    svc = _svc()
    _require_configured(svc)
    _require_live_confirm(svc, confirm_live)
    try:
        return await svc.close_position(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/broker/positions")
async def close_all_broker_positions(
    confirm_live: bool = Query(default=False),
    confirm_close_all: bool = Query(default=False),
    cancel_orders: bool = Query(default=True),
) -> list:
    """Liquida todo el portafolio (`alpaca position close-all`) — destructivo."""
    svc = _svc()
    _require_configured(svc)
    _require_live_confirm(svc, confirm_live)
    if not confirm_close_all:
        raise HTTPException(
            status_code=400,
            detail="Operación destructiva: pasa confirm_close_all=true",
        )
    try:
        return await svc.close_all_positions(cancel_orders=cancel_orders)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/broker/orders", response_model=list[BrokerOrderResult])
async def broker_orders(
    status: str = Query(default="open", description="open | closed | all"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[BrokerOrderResult]:
    svc = _svc()
    _require_configured(svc)
    try:
        return await svc.list_orders(status=status, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/broker/orders", response_model=BrokerOrderResult)
async def submit_broker_order(
    request: BrokerOrderRequest,
    confirm_live: bool = Query(default=False, description="Obligatorio True en cuenta LIVE"),
) -> BrokerOrderResult:
    """Envía una orden individual (auto client_order_id si no viene)."""
    svc = _svc()
    _require_configured(svc)
    _require_live_confirm(svc, confirm_live)
    result = await svc.submit_one(request)
    if result.error:
        raise HTTPException(status_code=502, detail=result.error)
    return result


@router.delete("/broker/orders/{order_id}")
async def cancel_broker_order(
    order_id: str,
    confirm_live: bool = Query(default=False),
) -> dict:
    """Cancela una orden abierta (`alpaca order cancel`)."""
    svc = _svc()
    _require_configured(svc)
    _require_live_confirm(svc, confirm_live)
    try:
        return await svc.cancel_order(order_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/broker/orders")
async def cancel_all_broker_orders(
    confirm_live: bool = Query(default=False),
    confirm_cancel_all: bool = Query(default=False),
) -> list:
    """Cancela todas las órdenes abiertas (`alpaca order cancel-all`) — destructivo."""
    svc = _svc()
    _require_configured(svc)
    _require_live_confirm(svc, confirm_live)
    if not confirm_cancel_all:
        raise HTTPException(
            status_code=400,
            detail="Operación destructiva: pasa confirm_cancel_all=true",
        )
    try:
        return await svc.cancel_all_orders()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
