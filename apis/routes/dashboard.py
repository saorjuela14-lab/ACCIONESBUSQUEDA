"""Terminal dashboard API."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.portfolio_snapshot_repository import PortfolioSnapshotRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.dashboard import (
    ClientAccountView,
    PortfolioDashboardSlice,
    TerminalDashboard,
    TickerOpportunity,
    WatchlistMatrixRow,
)
from domain.enums import InvestmentRecommendation
from providers.market.factory import get_market_provider
from services.market_dashboard_service import MarketDashboardService
from services.provider_diagnostics import get_providers_status
from services.watchlist_matrix_service import WatchlistMatrixService

router = APIRouter()

_BUY_RECS = {InvestmentRecommendation.BUY.value, InvestmentRecommendation.STRONG_BUY.value}
_SELL_RECS = {InvestmentRecommendation.SELL.value, InvestmentRecommendation.STRONG_SELL.value}


def _memory_to_opportunities(memory: list) -> tuple[list[TickerOpportunity], list[TickerOpportunity]]:
    """Derive top opportunities and risks from recent investment memory."""
    seen: set[str] = set()
    opportunities: list[TickerOpportunity] = []
    risks: list[TickerOpportunity] = []

    for m in memory:
        t = m.ticker.upper()
        if t in seen:
            continue
        seen.add(t)
        agg = sum(m.scores.values()) / len(m.scores) if m.scores else 0.0
        rec = (m.recommendation or "").lower()
        item = TickerOpportunity(
            ticker=t,
            recommendation=rec.upper(),
            confidence=m.confidence,
            score=round(agg, 2),
            reason=(m.thesis or m.expected_outcome or "")[:160],
        )
        if rec in _BUY_RECS or (agg > 5 and rec not in _SELL_RECS):
            opportunities.append(item)
        elif rec in _SELL_RECS or agg < -5:
            risks.append(item)

    opportunities.sort(key=lambda x: x.confidence * max(x.score, 0), reverse=True)
    risks.sort(key=lambda x: x.confidence * abs(min(x.score, 0)), reverse=True)
    return opportunities[:8], risks[:8]


def _redact_for_client(dash: TerminalDashboard, client_view: ClientAccountView) -> TerminalDashboard:
    """Clients never see firm book totals, positions, or research surfaces."""
    # Performance % only — strip dollar totals / cash / weights
    perf = None
    if dash.portfolio is not None:
        perf = PortfolioDashboardSlice(
            portfolio_id=None,
            name="Rendimiento Monarch",
            mode=dash.portfolio.mode,
            initial_capital=0.0,
            cash=0.0,
            total_value=0.0,
            return_pct=dash.portfolio.return_pct,
            sharpe=None,
            sortino=None,
            max_drawdown=None,
            diversification_score=None,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
    news = dash.news_highlights if client_view.has_invested else []
    return dash.model_copy(
        update={
            "portfolio": perf,
            "client_view": client_view,
            "watchlist": [],
            "top_opportunities": [],
            "top_risks": [],
            "recently_analyzed": [],
            "active_alerts": [],
            "news_highlights": news,
            "provider_health": {},
        }
    )


@router.get("/dashboard", response_model=TerminalDashboard)
async def get_terminal_dashboard(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> TerminalDashboard:
    # Everyone monitors the firm desk book (`monarch`). Only desk may bootstrap/sync Alpaca.
    book_org = scope.book_org_id()

    watchlist_items = await WatchlistRepository(session).list_active(org_id=book_org)
    watchlist = [w.ticker for w in watchlist_items]

    alerts_raw = await AlertRepository(session).list_unacknowledged(15, org_id=book_org)
    alerts = [f"[{a.severity.value}] {a.ticker}: {a.title}" for a in alerts_raw[:15]]

    portfolio_slice = None
    bootstrap_note = None
    from providers.market.factory import get_market_provider
    from services.portfolio_bootstrap_service import PortfolioBootstrapService
    from services.portfolio_service import PortfolioService

    svc = PortfolioService(PortfolioRepository(session), get_market_provider())
    p = None
    source = "none"
    try:
        if scope.is_desk:
            p, source = await PortfolioBootstrapService(svc).ensure_portfolio(
                org_id=book_org,
                allow_alpaca=True,
                default_name="Portafolio CEO",
                default_cash=22.0,
            )
            if source == "alpaca":
                bootstrap_note = (
                    "Portafolio recreado desde Alpaca (la DB SQLite se reinicia en cada redeploy). "
                    "Para persistencia permanente usa Postgres en DATABASE_URL."
                )
            elif source == "default":
                bootstrap_note = (
                    "Portafolio por defecto recreado tras reinicio del servidor. "
                    "Conecta Alpaca o usa Postgres para no perder datos."
                )
        else:
            # Clients: never seed fake capital — only load real firm book for return %
            portfolios = await PortfolioRepository(session).list_all(org_id=book_org)
            p = sorted(portfolios, key=lambda x: x.updated_at, reverse=True)[0] if portfolios else None
            source = "existing" if p else "none"
    except Exception:
        portfolios = await PortfolioRepository(session).list_all(org_id=book_org)
        p = sorted(portfolios, key=lambda x: x.updated_at, reverse=True)[0] if portfolios else None
        source = "existing" if p else "none"

    if p:
        try:
            p = await svc.refresh_prices(p.id)
        except Exception:
            # Keep DB portfolio even if market quotes fail
            pass
        try:
            metrics = await svc.compute_metrics(p)
        except Exception:
            metrics = {}
        sector_w: dict[str, float] = {}
        country_w: dict[str, float] = {}
        cap_w: dict[str, float] = {"large": 0.0, "mid": 0.0, "small": 0.0}
        try:
            for pos in p.positions:
                try:
                    q = await get_market_provider().get_quote(pos.ticker)
                except Exception:
                    q = {}
                sec = q.get("sector") or "Unknown"
                country = q.get("country") or "Unknown"
                mcap = float(q.get("market_cap") or 0)
                val = (pos.current_price or pos.average_cost) * pos.shares
                sector_w[sec] = sector_w.get(sec, 0) + val
                country_w[country] = country_w.get(country, 0) + val
                if mcap >= 10e9:
                    cap_w["large"] += val
                elif mcap >= 2e9:
                    cap_w["mid"] += val
                else:
                    cap_w["small"] += val
        except Exception:
            pass
        total = p.total_value or p.initial_capital
        if total:
            sector_w = {k: round(v / total * 100, 1) for k, v in sector_w.items()}
            country_w = {k: round(v / total * 100, 1) for k, v in country_w.items()}
            cap_w = {k: round(v / total * 100, 1) for k, v in cap_w.items()}
        unrealized = sum(
            ((pos.current_price or pos.average_cost) - pos.average_cost) * pos.shares
            for pos in p.positions
        )
        portfolio_slice = PortfolioDashboardSlice(
            portfolio_id=p.id,
            name=p.name,
            mode=p.mode.value,
            initial_capital=p.initial_capital,
            cash=p.cash,
            total_value=total,
            return_pct=p.return_pct,
            sharpe=metrics.get("sharpe"),
            sortino=metrics.get("sortino"),
            max_drawdown=metrics.get("max_drawdown"),
            diversification_score=min(100, len(p.positions) * 20),
            sector_weights=sector_w,
            country_weights=country_w,
            cap_exposure=cap_w,
            currency_exposure={"USD": 100.0} if total else {},
            unrealized_pnl=round(unrealized, 2),
        )
        try:
            await PortfolioSnapshotRepository(session).save(
                p.id, total, p.return_pct, p.cash
            )
        except Exception:
            pass

    memory = await InvestmentMemoryRepository(session).list_recent(limit=20)
    recently = [m.ticker for m in memory] if memory else []
    opportunities, risks = _memory_to_opportunities(memory)

    try:
        provider_health = await get_providers_status()
    except Exception:
        provider_health = {}
    if bootstrap_note:
        provider_health = dict(provider_health or {})
        provider_health["portfolio_bootstrap"] = bootstrap_note

    svc_mkt = MarketDashboardService()
    dash = await svc_mkt.build(
        watchlist=watchlist,
        alerts=alerts,
        portfolio_slice=portfolio_slice,
        opportunities=opportunities,
        risks=risks,
        recently_analyzed=recently,
        provider_health=provider_health,
    )

    if scope.is_client and scope.org_id and scope.user_id:
        from services.capital_request_service import CapitalRequestService

        firm_ret = portfolio_slice.return_pct if portfolio_slice else None
        try:
            summary = await CapitalRequestService(session).client_capital_summary(
                org_id=scope.org_id,
                user_id=scope.user_id,
                firm_return_pct=firm_ret,
            )
            client_view = ClientAccountView(**summary)
        except Exception:
            client_view = ClientAccountView(
                has_invested=False,
                mode="prospect",
                firm_return_pct=firm_ret,
                note="No se pudo cargar tu capital; muestra solo el rendimiento de la cuenta.",
            )
        return _redact_for_client(dash, client_view)

    return dash


@router.get("/dashboard/watchlist-matrix", response_model=list[WatchlistMatrixRow])
async def get_watchlist_matrix(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> list[WatchlistMatrixRow]:
    if scope.is_client:
        return []
    watchlist = await WatchlistRepository(session).list_active(org_id=scope.read_org_id())
    tickers = [w.ticker for w in watchlist]
    memory = await InvestmentMemoryRepository(session).latest_by_ticker(tickers)
    return await WatchlistMatrixService(get_market_provider()).build(watchlist, memory)
