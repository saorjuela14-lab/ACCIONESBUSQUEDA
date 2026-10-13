"""Ops desk API — kill switch, audit, reconcile, lifecycle, auto-execute policy."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from apis.deps import OrgScope, get_org_scope
from domain.ops import (
    AuditEvent,
    AutoExecutePolicy,
    KillSwitchState,
    LifecycleScanReport,
    PortfolioRiskMetrics,
    ReconcileReport,
)
from services.autopilot_service import AutopilotService
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.auto_execute_service import AutoExecuteService
from services.kill_switch_service import KillSwitchService
from services.live_safety import trading_mode_label
from services.portfolio_risk_metrics_service import PortfolioRiskMetricsService
from services.position_lifecycle_service import PositionLifecycleService
from services.reconcile_service import ReconcileService
from database.repositories.ops_repository import OpsFlagRepository
from domain.ops import utc_now

router = APIRouter()


def _ops_db_host() -> str | None:
    from database.engine import db_snapshot

    return db_snapshot().get("host")


async def _lease_status(session: AsyncSession) -> dict:
    from services.db_lease import snapshot_leases

    try:
        return await snapshot_leases(session)
    except Exception as exc:
        return {"error": str(exc)}


class KillSwitchRequest(BaseModel):
    confirm: bool = False
    reason: str = "panic flat"
    flatten: bool = False
    actor: str = "user"


class ThesisInvalidateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=500)


class AutopilotRunRequest(BaseModel):
    execute_trades: bool | None = Field(
        default=None,
        description="None = usa AUTO_EXECUTE_TRADES; true/false fuerza el ciclo",
    )
    session_label: str = "autopilot"


class PromoteLiveRequest(BaseModel):
    confirm: bool = False
    note: str = "Promovido tras paper soak"
    min_paper_fills: int = Field(default=3, ge=0)
    force: bool = Field(
        default=False,
        description="Saltar paper soak cuando el dueño autoriza firma autónoma LIVE",
    )


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


@router.get("/ops/audit", response_model=None)
async def list_audit(
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    from domain.pagination import Page

    items, total = await AuditService(session).recent_page(
        limit=limit, offset=offset, action=action
    )
    return Page.of(items, total=total, limit=limit, offset=offset)


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


@router.post("/ops/holdings/review")
async def holdings_strategy_review(
    execute_exits: bool | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reformulate open-position thesis, raise TP / tighten stops, prefer take-profit exits."""
    from services.holdings_strategy_review_service import HoldingsStrategyReviewService

    settings = get_settings()
    do_exit = settings.lifecycle_auto_exit if execute_exits is None else execute_exits
    review = await HoldingsStrategyReviewService(session).review(execute_exits=do_exit)
    scan = await PositionLifecycleService(session).scan(execute_exits=do_exit)
    return {"review": review, "lifecycle": scan}


@router.post("/ops/intraday/flat")
async def intraday_flat(
    force: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cierra todas las acciones para no llevar riesgo overnight (política intraday-only)."""
    from services.intraday_flat_service import IntradayFlatService

    return await IntradayFlatService(session).run(
        force=force,
        reason="manual_api" if force else None,
        actor="user",
    )


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
    ok, reason = await svc.can_auto_trade_async()
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
    ok, reason = await auto.can_auto_trade_async()
    promo = await OpsFlagRepository(session).get_json("paper_promotion")
    from services.deposited_capital_service import deposited_base_status, get_deposited_base

    pf_init = None
    try:
        from database.repositories.portfolio_repository import PortfolioRepository

        rows = await PortfolioRepository(session).list_all()
        if rows:
            pf_init = getattr(rows[0], "initial_capital", None)
    except Exception:
        pf_init = None
    deposited = await get_deposited_base(portfolio_initial=pf_init)
    deposited_payload = deposited_base_status(deposited)
    warnings = list(deposited_payload.get("warnings") or [])
    return {
        "kill_switch": ks.model_dump(mode="json"),
        "firm_autonomy": settings.firm_autonomy,
        "autopilot_interval_minutes": settings.effective_autopilot_interval_minutes,
        "auto_execute": {
            "allowed": ok,
            "reason": reason,
            "entries_allowed": ok,
            "exits_only": not bool(settings.live_entries_enabled),
            "policy": auto.policy().model_dump(mode="json"),
        },
        "paper_promotion": promo or {"promoted": False},
        "lifecycle_enabled": settings.lifecycle_enabled,
        "lifecycle_auto_exit": settings.lifecycle_auto_exit,
        "holdings_strategy_review_enabled": settings.holdings_strategy_review_enabled,
        "intraday_only_enabled": settings.intraday_only_enabled,
        "intraday_flat_minutes_before_close": settings.intraday_flat_minutes_before_close,
        "intraday_flat_cron": settings.intraday_flat_cron,
        "intraday_flat_winners_only": settings.intraday_flat_winners_only,
        "intraday_flat_min_pnl_pct": settings.intraday_flat_min_pnl_pct,
        "intraday_2r_hold_enabled": settings.intraday_2r_hold_enabled,
        "intraday_carry_max_loss_pct": settings.intraday_carry_max_loss_pct,
        "live_entries_enabled": bool(settings.live_entries_enabled),
        "live_max_entries_per_day": settings.live_max_entries_per_day,
        "live_submit_fail_pause": settings.live_submit_fail_pause,
        "deposited_base": deposited_payload,
        "deposited_brake_pct": settings.deposited_brake_pct,
        "warnings": warnings,
        "app_env": settings.app_env,
        "trading_mode": trading_mode_label(settings),
        "risk_discipline": {
            "max_risk_pct": settings.auto_execute_max_risk_pct,
            "micro_max_risk_pct": settings.auto_execute_micro_max_risk_pct,
            "post_stop_cooldown_minutes": settings.auto_execute_post_stop_cooldown_minutes,
            "max_position_pct": settings.auto_execute_max_position_pct,
            "trail_arm_profit_pct": settings.lifecycle_trail_arm_profit_pct,
            "micro_stop_pct": settings.lifecycle_micro_default_stop_pct,
            "micro_target_pct": settings.lifecycle_micro_default_target_pct,
            "micro_trailing_pct": settings.lifecycle_micro_trailing_pct,
        },
        "reconcile_auto_sync": settings.reconcile_auto_sync,
        "risk": {
            "max_var_pct": settings.risk_max_var_pct,
            "max_beta": settings.risk_max_portfolio_beta,
            "max_sector_pct": settings.risk_max_sector_pct,
        },
        "leases": await _lease_status(session),
        "crypto_paper": await _crypto_paper_status(session),
        "strategy_a_eligibility": _strategy_a_eligibility_status(),
    }


def _strategy_a_eligibility_status(broker: Any = None) -> dict:
    from services.multiasset.crypto_filters import eligibility_threshold

    if broker is None:
        try:
            from services.multiasset.paper_broker import get_beta_broker_provider

            broker = get_beta_broker_provider()
        except Exception:
            broker = None
    return eligibility_threshold(broker)


async def _crypto_paper_status(session: AsyncSession) -> dict:
    flags = OpsFlagRepository(session)
    book = await flags.get_json("crypto_paper_book")
    armed = await flags.get_json("crypto_strategy_a_armed")
    thresh = _strategy_a_eligibility_status()
    return {
        "legacy_engine": "off",
        "strategy_a_armed": bool((armed or {}).get("armed")) if isinstance(armed, dict) else False,
        "inherited_count_toward_25pct": True,
        "inherited_notional_usd": (book or {}).get("inherited_notional_usd"),
        "inherited_symbols": (book or {}).get("inherited_symbols") or [],
        "book_deviation": (book or {}).get("book_deviation")
        or (
            "inherited August lots count toward the 25% paper crypto sleeve; "
            "Strategy A does not manage them"
        ),
        "eligibility_threshold": thresh,
    }


class CryptoKillResetRequest(BaseModel):
    confirm: bool = False
    reason: str = Field(min_length=3, max_length=240)


@router.post("/ops/crypto/kill-reset")
async def reset_crypto_allocation_kill(
    body: CryptoKillResetRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Audited reset of Strategy A allocation kill. Does not submit orders."""
    scope.require_desk()
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    from services.multiasset.crypto_owned import desk_actor
    from services.multiasset.crypto_risk import reset_allocation_kill

    flags = OpsFlagRepository(session)
    mark = await flags.get_json("crypto_strategy_a_risk")
    wealth = float(mark.get("wealth_usd") or mark.get("crypto_usd") or 0)
    actor = desk_actor(scope)
    updated = reset_allocation_kill(
        mark, actor=actor, reason=body.reason, current_wealth=wealth
    )
    await flags.set_json("crypto_strategy_a_risk", updated)
    return {
        "ok": True,
        "kill_active": False,
        "reset": updated.get("kill_reset"),
        "peak_wealth_usd": updated.get("peak_wealth_usd"),
    }


@router.get("/ops/autopilot/last")
async def last_autopilot_cycle(session: AsyncSession = Depends(get_session)) -> dict:
    """Read-only snapshot of the last firm Autopilot cycle (hora, resultado, mensaje)."""
    data = await OpsFlagRepository(session).get_json("firm_autopilot_last_cycle")
    body = data or {"at": None, "result": None, "message": "sin ciclo registrado"}
    try:
        from services.db_lease import LEASE_LIVE_STOCKS, snapshot_lease

        lease = (await snapshot_lease(session, LEASE_LIVE_STOCKS)).as_dict()
        body["lease"] = lease
        body["lease_owner"] = lease.get("lease_owner")
        body["lease_expires_at"] = lease.get("lease_expires_at")
        body["lease_misses_consecutive"] = lease.get("lease_misses_consecutive")
    except Exception:
        body.setdefault("lease", {"name": "live_stocks", "error": "snapshot_failed"})
    return body


@router.get("/ops/multiasset/activities")
async def paper_multiasset_activities(
    types: str = Query(default="FILL,CFEE", description="Alpaca activity types, comma-separated"),
    after: str | None = Query(
        default=None,
        description=(
            "Exclusive lower bound on created_at (after). "
            "YYYY-MM-DD becomes 00:00:00Z; full timestamps are normalized to UTC Z."
        ),
    ),
    until: str | None = Query(
        default=None,
        description=(
            "Exclusive upper bound on created_at (before). "
            "YYYY-MM-DD becomes the next UTC midnight so the requested day is included. "
            "CFEE extends until one extra UTC day (until_effective)."
        ),
    ),
    page_token: str | None = Query(default=None, description="Alpaca page_token (last activity id)"),
    direction: str = Query(default="desc", description="asc | desc"),
    page_size: int = Query(default=100, ge=1, le=100),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Read-only FILL/CFEE from the Multi-Asset PAPER account. Mesa session required."""
    scope.require_desk()
    from services.multiasset.paper_ops import normalize_activity_bound

    for label, value in (("after", after), ("until", until)):
        if value:
            try:
                normalize_activity_bound(value, kind=label)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} debe ser YYYY-MM-DD o un timestamp ISO UTC",
                ) from exc
    if (direction or "").strip().lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="direction debe ser asc o desc")
    from services.multiasset.paper_broker import MultiAssetNotPaperError, get_beta_broker_provider
    from services.multiasset.paper_ops import list_paper_activities

    try:
        broker = get_beta_broker_provider()
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        return await list_paper_activities(
            broker,
            types=types,
            after=after,
            until=until,
            page_size=page_size,
            page_token=page_token,
            direction=direction,
        )
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class LegacyFlattenRequest(BaseModel):
    confirm: bool = False
    dry_run: bool = True
    symbols: list[str] = Field(default_factory=list)


class EnableStrategyARequest(BaseModel):
    confirm: bool = False


def _paper_ops_broker():
    from services.multiasset.paper_broker import MultiAssetNotPaperError, get_beta_broker_provider

    try:
        return get_beta_broker_provider()
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ops/multiasset/positions")
async def paper_multiasset_positions(
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Read-only Alpaca PAPER positions. Mesa token. Refuses a LIVE client."""
    scope.require_desk()
    from services.multiasset.crypto_legacy import list_paper_positions
    from services.multiasset.paper_broker import MultiAssetNotPaperError

    broker = _paper_ops_broker()
    try:
        return await list_paper_positions(broker)
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ops/multiasset/orders")
async def paper_multiasset_orders(
    status: str = Query(default="open", description="open | closed | all"),
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Read-only Alpaca PAPER orders. Mesa token. Refuses a LIVE client."""
    scope.require_desk()
    from services.multiasset.crypto_legacy import list_paper_orders
    from services.multiasset.paper_broker import MultiAssetNotPaperError

    broker = _paper_ops_broker()
    try:
        return await list_paper_orders(
            broker, status=status, page_token=page_token, page_size=page_size
        )
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ops/crypto/legacy-flatten")
async def crypto_legacy_flatten(
    body: LegacyFlattenRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Explicit PAPER flatten of inherited August lots. dry_run=true by default."""
    scope.require_desk()
    if not body.dry_run and not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required when dry_run=false")
    from services.multiasset.crypto_legacy import legacy_flatten
    from services.multiasset.crypto_owned import desk_actor
    from services.multiasset.paper_broker import MultiAssetNotPaperError
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    broker = _paper_ops_broker()
    actor = desk_actor(scope)
    try:
        return await legacy_flatten(
            broker,
            symbols=body.symbols,
            dry_run=body.dry_run,
            actor=actor,
            tracker=MultiAssetTradeTracker(session),
            flags=OpsFlagRepository(session),
        )
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ops/crypto/enable-strategy-a")
async def enable_crypto_strategy_a(
    body: EnableStrategyARequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Arm Strategy A after the mesa closes inherited lots. Paper only."""
    scope.require_desk()
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    from services.multiasset.crypto_legacy import require_paper_broker
    from services.multiasset.crypto_owned import (
        desk_actor,
        inherited_btc_eth_symbols,
        set_strategy_a_armed,
    )
    from services.multiasset.crypto_risk import MAX_CRYPTO_EQUITY_PCT
    from services.multiasset.paper_broker import MultiAssetNotPaperError
    from services.multiasset.trade_tracker import MultiAssetTradeTracker

    broker = _paper_ops_broker()
    try:
        require_paper_broker(broker)
    except MultiAssetNotPaperError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    tracker = MultiAssetTradeTracker(session)
    open_lots = await tracker.list_open(desk="crypto")
    broker_syms: list[str] = []
    try:
        for pos in await broker.get_positions() or []:
            if isinstance(pos, dict):
                broker_syms.append(str(pos.get("symbol") or ""))
            else:
                broker_syms.append(str(getattr(pos, "symbol", "") or ""))
    except Exception:
        broker_syms = []
    inherited_be = inherited_btc_eth_symbols(open_lots, broker_syms)
    if inherited_be:
        raise HTTPException(
            status_code=409,
            detail={"error": "inherited_btc_eth", "symbols": inherited_be},
        )
    equity = 0.0
    try:
        acct = await broker.get_account()
        if isinstance(acct, dict):
            equity = float(acct.get("equity") or acct.get("portfolio_value") or 0)
        else:
            equity = float(getattr(acct, "equity", 0) or 0)
    except Exception:
        equity = 0.0
    allocation = max(0.0, equity * MAX_CRYPTO_EQUITY_PCT / 100.0)
    actor = desk_actor(scope)
    flags = OpsFlagRepository(session)
    payload = await set_strategy_a_armed(
        flags, armed=True, actor=actor, allocation_usd=allocation, equity_usd=equity
    )
    return {"ok": True, "paper": True, **payload}


@router.post("/ops/autopilot/run")
async def autopilot_run(
    body: AutopilotRunRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ciclo completo de la firma: reconcile → risk → lifecycle → picks → (auto)execute."""
    req = body or AutopilotRunRequest()
    return await AutopilotService(session).run(
        session_label=req.session_label,
        execute_trades=req.execute_trades,
        actor="user_autopilot",
    )


@router.get("/ops/journal")
async def list_trade_journal(
    limit: int = Query(default=40, ge=1, le=200),
    status: str | None = Query(default=None, description="open|closed|all"),
    days: int = Query(default=90, ge=1, le=730),
    session: AsyncSession = Depends(get_session),
):
    """Durable trade journal (open→close) for desk transparency."""
    from services.trade_journal_service import TradeJournalService

    svc = TradeJournalService(session)
    if status == "open":
        items = await svc.list_open()
    elif status == "closed":
        items = await svc.list_closed(limit=limit, days=days)
    else:
        items = await svc.list_recent(limit=limit)
    return {"items": items, "count": len(items), "days": days}


@router.get("/ops/track-record")
async def get_track_record(
    window_days: int = Query(default=90, ge=7, le=730),
    session: AsyncSession = Depends(get_session),
):
    """Win rate of closed journal trades + evaluated investment memory."""
    from services.track_record_service import TrackRecordService

    return await TrackRecordService(session).summary(window_days=window_days)


@router.get("/ops/month-report", response_model=None)
async def get_month_report(
    window_days: int = Query(default=30, ge=7, le=120),
    session: AsyncSession = Depends(get_session),
):
    """CEO monthly desk report: equity vs $20, true 2R, stagnation, agents, vs SPY."""
    from services.month_report_service import MonthReportService

    return await MonthReportService(session).build(window_days=window_days)


@router.get("/ops/agent-effectiveness", response_model=None)
async def get_agent_effectiveness(
    window_days: int = Query(default=1, ge=1, le=730),
    score_threshold: float = Query(default=5.0, ge=0.0, le=50.0),
    session: AsyncSession = Depends(get_session),
):
    """Per-agent directional hit rate + desk thesis hit rate (decision quality)."""
    from services.agent_effectiveness_service import AgentEffectivenessService
    from services.desk_learning_service import DeskLearningService
    from services.trade_close_review_service import TradeCloseReviewService

    summary = await AgentEffectivenessService(
        session, score_threshold=score_threshold
    ).summary(window_days=window_days)
    lessons = await DeskLearningService(session).snapshot()
    payload = summary.model_dump(mode="json")
    payload["lessons"] = lessons
    payload["last_close_review"] = await TradeCloseReviewService(session).latest()
    return payload


@router.get("/ops/lessons")
async def get_desk_lessons(session: AsyncSession = Depends(get_session)):
    """Active lessons (avoid tickers + per-agent justification errors)."""
    from services.desk_learning_service import DeskLearningService

    return await DeskLearningService(session).snapshot()


@router.post("/ops/learn")
async def run_daily_learning(session: AsyncSession = Depends(get_session)):
    """Force same-day memory evaluation + lesson write (ops)."""
    from database.repositories.investment_memory_repository import InvestmentMemoryRepository
    from providers.market.factory import get_market_provider
    from services.memory_evaluation_service import MemoryEvaluationService
    from services.trade_close_review_service import TradeCloseReviewService

    result = await MemoryEvaluationService(
        InvestmentMemoryRepository(session),
        get_market_provider(),
    ).evaluate_pending()
    closes = await TradeCloseReviewService(session).review_unreviewed_closes()
    result["close_reviews"] = closes
    result["stagnation_avoids"] = await TradeCloseReviewService(session).refresh_stagnation_avoids()
    return result


@router.post("/ops/autopilot/promote-live")
async def promote_live(
    body: PromoteLiveRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Marca paper soak completo. LIVE aún requiere AUTO_EXECUTE_LIVE=true."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm=true requerido")
    # Soft check: count successful auto_execute / buy_submit in audit
    recent = await AuditService(session).recent(limit=100)
    paper_fills = sum(
        1
        for e in recent
        if e.success
        and e.paper is True
        and e.action in ("buy_submit", "auto_execute", "lifecycle_exit")
    )
    settings = get_settings()
    skip_soak = body.force or settings.firm_autonomy
    if not skip_soak and paper_fills < body.min_paper_fills:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Paper soak incompleto: {paper_fills}/{body.min_paper_fills} eventos paper. "
                "Opera en Alpaca Paper primero o envía force=true / FIRM_AUTONOMY=true."
            ),
        )
    payload = {
        "promoted": True,
        "promoted_at": utc_now().isoformat(),
        "note": body.note,
        "paper_fills_seen": paper_fills,
    }
    await OpsFlagRepository(session).set_json("paper_promotion", payload)
    await AuditService(session).record(
        "auto_execute",
        actor="user",
        message=f"Paper→LIVE promotion flag set ({body.note})",
        payload=payload,
    )
    return {
        "ok": True,
        "promotion": payload,
        "next": "Define AUTO_EXECUTE_LIVE=true (y preferible AUTO_EXECUTE_TRADES=true) para LIVE.",
    }
