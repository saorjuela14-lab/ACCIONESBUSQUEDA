"""Ops desk API — kill switch, audit, reconcile, lifecycle, auto-execute policy."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from domain.ops import (
    AuditEvent,
    AutoExecutePolicy,
    KillSwitchState,
    LifecycleScanReport,
    PortfolioRiskMetrics,
    ReconcileReport,
)
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.auto_execute_service import AutoExecuteService
from services.kill_switch_service import KillSwitchService
from services.portfolio_risk_metrics_service import PortfolioRiskMetricsService
from services.position_lifecycle_service import PositionLifecycleService
from services.reconcile_service import ReconcileService

router = APIRouter()


class KillSwitchRequest(BaseModel):
    confirm: bool = False
    reason: str = "panic flat"
    flatten: bool = True
    actor: str = "user"


class ThesisInvalidateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=500)


@router.get("/ops/kill-switch", response_model=KillSwitchState)
async def get_kill_switch(session: AsyncSession = Depends(get_session)) -> KillSwitchState:
    return await KillSwitchService(session).status()


@router.post("/ops/kill-switch/on", response_model=KillSwitchState)
async def activate_kill_switch(
    body: KillSwitchRequest,
    session: AsyncSession = Depends(get_session),
) -> KillSwitchState:
    try:
        return await KillSwitchService(session).activate(
            reason=body.reason,
            actor=body.actor,
            flatten=body.flatten,
            confirm=body.confirm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ops/kill-switch/off", response_model=KillSwitchState)
async def deactivate_kill_switch(
    body: KillSwitchRequest,
    session: AsyncSession = Depends(get_session),
) -> KillSwitchState:
    try:
        return await KillSwitchService(session).deactivate(
            actor=body.actor,
            confirm=body.confirm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ops/audit", response_model=list[AuditEvent])
async def list_audit(
    limit: int = Query(default=40, ge=1, le=200),
    action: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AuditEvent]:
    return await AuditService(session).recent(limit=limit, action=action)


@router.post("/ops/reconcile", response_model=ReconcileReport)
async def reconcile_books(
    sync: bool = True,
    portfolio_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> ReconcileReport:
    return await ReconcileService(session).reconcile(sync=sync, portfolio_id=portfolio_id)


@router.post("/ops/lifecycle/scan", response_model=LifecycleScanReport)
async def lifecycle_scan(
    execute_exits: bool | None = None,
    session: AsyncSession = Depends(get_session),
) -> LifecycleScanReport:
    settings = get_settings()
    do_exit = settings.lifecycle_auto_exit if execute_exits is None else execute_exits
    return await PositionLifecycleService(session).scan(execute_exits=do_exit)


@router.post("/ops/lifecycle/invalidate")
async def invalidate_thesis(
    body: ThesisInvalidateRequest,
    session: AsyncSession = Depends(get_session),
):
    m = await PositionLifecycleService(session).invalidate_thesis(body.symbol, body.reason)
    if not m:
        raise HTTPException(status_code=404, detail=f"No hay mandato abierto para {body.symbol}")
    # Immediately scan to exit if auto-exit on
    report = await PositionLifecycleService(session).scan(
        execute_exits=get_settings().lifecycle_auto_exit
    )
    return {"mandate": m, "scan": report}


@router.get("/ops/auto-execute/policy", response_model=AutoExecutePolicy)
async def auto_execute_policy(session: AsyncSession = Depends(get_session)) -> AutoExecutePolicy:
    svc = AutoExecuteService(session)
    policy = svc.policy()
    ok, reason = svc.can_auto_trade()
    policy.promotion_note = f"{policy.promotion_note} Estado: {reason} (allowed={ok})"
    return policy


@router.get("/ops/risk-metrics", response_model=PortfolioRiskMetrics)
async def ops_risk_metrics() -> PortfolioRiskMetrics:
    broker = AlpacaOrderService()
    if not broker.is_configured():
        return PortfolioRiskMetrics(warnings=["Alpaca no configurada"])
    account = await broker.get_account()
    positions = await broker.get_positions()
    return await PortfolioRiskMetricsService().compute(
        positions,
        equity=account.equity or account.portfolio_value or 0.0,
    )


@router.get("/ops/status")
async def ops_status(session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    ks = await KillSwitchService(session).status()
    auto = AutoExecuteService(session)
    ok, reason = auto.can_auto_trade()
    return {
        "kill_switch": ks.model_dump(mode="json"),
        "auto_execute": {
            "allowed": ok,
            "reason": reason,
            "policy": auto.policy().model_dump(mode="json"),
        },
        "lifecycle_enabled": settings.lifecycle_enabled,
        "reconcile_auto_sync": settings.reconcile_auto_sync,
        "risk": {
            "max_var_pct": settings.risk_max_var_pct,
            "max_beta": settings.risk_max_portfolio_beta,
            "max_sector_pct": settings.risk_max_sector_pct,
        },
    }
