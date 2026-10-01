"""Multi-asset beta API — gold / forex / crypto paper desks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
from config.settings import get_settings
from database.engine import get_session
from domain.multiasset import AssetDeskId, MultiAssetOrderRequest
from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.trade_tracker import MultiAssetTradeTracker

router = APIRouter()


def _enabled() -> None:
    if not get_settings().multiasset_beta_enabled:
        raise HTTPException(status_code=503, detail="Módulo multi-asset beta desactivado")


@router.get("/beta/multiasset/board")
async def desk_board(session: AsyncSession = Depends(get_session)):
    """P&L, risk used, director weights and specialist stats — paper mesa only."""
    _enabled()
    from services.multiasset.allocator import allocate
    from services.multiasset.risk_engine import FLAG_CYCLE, MultiAssetRiskDesk

    svc = MultiAssetDeskService(session)
    tracker = MultiAssetTradeTracker(session)
    risk = MultiAssetRiskDesk(session)
    records = {}
    closed_pnl = 0.0
    open_risk = 0.0
    agents = []
    for desk in ("gold", "forex", "crypto"):
        tr = await tracker.track_record(desk=desk, window_days=90)
        records[desk] = tr
        closed_pnl += float(tr.trades_total_pnl_usd or 0)
        for t in await tracker.list_open(desk=desk):
            if t.entry_price and t.stop_hint and t.qty:
                open_risk += max(0.0, (float(t.entry_price) - float(t.stop_hint)) * float(t.qty))
        for a in tr.agents:
            agents.append({"desk": desk, **a.model_dump(mode="json")})
    plan = allocate(records=records)
    status_gold = await svc.status("gold")
    equity = float(status_gold.equity or 0)
    snap = await risk.snapshot(equity=equity)
    from database.repositories.ops_repository import OpsFlagRepository

    last = await OpsFlagRepository(session).get_json(FLAG_CYCLE)
    return {
        "paper": True,
        "live_untouched": True,
        "leverage": 1.0,
        "pnl_closed_usd": round(closed_pnl, 2),
        "risk_used_usd": round(open_risk, 2),
        "equity": equity,
        "cash": status_gold.cash,
        "director": plan,
        "risk": snap,
        "agents": agents,
        "last_cycle": last,
        "desks": {d: records[d].model_dump(mode="json") for d in records},
    }


@router.get("/beta/multiasset/last-cycle")
async def last_multiasset_cycle(session: AsyncSession = Depends(get_session)):
    _enabled()
    from database.repositories.ops_repository import OpsFlagRepository
    from services.multiasset.risk_engine import FLAG_CYCLE

    return await OpsFlagRepository(session).get_json(FLAG_CYCLE)


@router.get("/beta/multiasset/strategy-a/eligibility")
async def strategy_a_eligibility(scope: OrgScope = Depends(get_org_scope)):
    """Read-only Strategy A gate file. Mesa only. Never recomputes the backtest."""
    _enabled()
    scope.require_desk()
    from services.multiasset.crypto_eligibility import EligibilityClosed, public_payload

    try:
        return public_payload()
    except EligibilityClosed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/beta/multiasset/desks")
async def list_desks():
    _enabled()
    return {"beta": True, "desks": MultiAssetDeskService().list_desks()}


@router.get("/beta/multiasset/{desk}/status")
async def desk_status(desk: AssetDeskId, session: AsyncSession = Depends(get_session)):
    _enabled()
    try:
        return await MultiAssetDeskService(session).status(desk)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/beta/multiasset/{desk}/brief/{symbol}")
async def desk_brief(desk: AssetDeskId, symbol: str, session: AsyncSession = Depends(get_session)):
    _enabled()
    try:
        return await MultiAssetDeskService(session).brief(desk, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/beta/multiasset/execute")
async def execute_order(
    body: MultiAssetOrderRequest,
    session: AsyncSession = Depends(get_session),
):
    _enabled()
    try:
        return await MultiAssetDeskService(session).execute(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Orden falló: {exc}") from exc


@router.get("/beta/multiasset/history")
async def history(
    desk: AssetDeskId | None = None,
    limit: int = Query(default=40, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    _enabled()
    items = await MultiAssetDeskService(session).history(desk=desk, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/beta/multiasset/trades")
async def list_trades(
    desk: AssetDeskId | None = None,
    status: str = Query(default="all", pattern="^(open|closed|all)$"),
    limit: int = Query(default=40, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    _enabled()
    tracker = MultiAssetTradeTracker(session)
    open_t = await tracker.list_open(desk=desk) if status in ("open", "all") else []
    closed = (
        await tracker.list_closed(desk=desk, days=365, limit=limit)
        if status in ("closed", "all")
        else []
    )
    items = open_t + closed if status == "all" else (open_t if status == "open" else closed)
    return {"items": items[:limit], "count": len(items[:limit])}


@router.get("/beta/multiasset/track-record")
async def track_record(
    desk: AssetDeskId | None = None,
    window_days: int = Query(default=90, ge=7, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Win rate, brief hit rate, error patterns and per-agent effectiveness for beta desks."""
    _enabled()
    return await MultiAssetTradeTracker(session).track_record(desk=desk, window_days=window_days)


@router.post("/beta/multiasset/evaluate")
async def evaluate_open(
    min_age_hours: float | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Mark-to-market evaluate open trades (feedback sin cerrar)."""
    _enabled()
    hours = min_age_hours
    if hours is None:
        hours = float(get_settings().multiasset_eval_hours or 24)
    return await MultiAssetTradeTracker(session).evaluate_open_mtm(min_age_hours=hours)


@router.post("/beta/multiasset/autopilot/run")
async def run_multiasset_autopilot(session: AsyncSession = Depends(get_session)):
    """One capital-aware cycle across gold / forex / crypto paper desks."""
    _enabled()
    from services.multiasset.autopilot import MultiAssetAutopilotService

    return await MultiAssetAutopilotService(session).run(actor="api")
