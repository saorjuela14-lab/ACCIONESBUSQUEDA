"""Terminal dashboard API."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.portfolio_snapshot_repository import PortfolioSnapshotRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.dashboard import PortfolioDashboardSlice, TerminalDashboard, TickerOpportunity, WatchlistMatrixRow
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


@router.get("/dashboard", response_model=TerminalDashboard)
async def get_terminal_dashboard(
    session: AsyncSession = Depends(get_session),
) -> TerminalDashboard:
    watchlist_items = await WatchlistRepository(session).list_active()
    watchlist = [w.ticker for w in watchlist_items]

    alerts_raw = await AlertRepository(session).list_unacknowledged(15)
    alerts = [f"[{a.severity.value}] {a.ticker}: {a.title}" for a in alerts_raw[:15]]

    portfolio_slice = None
    portfolios = await PortfolioRepository(session).list_all()
    if portfolios:
        p = portfolios[0]
        from providers.market.factory import get_market_provider
        from services.portfolio_service import PortfolioService

        svc = PortfolioService(PortfolioRepository(session), get_market_provider())
        try:
            p = await svc.refresh_prices(p.id)
            metrics = await svc.compute_metrics(p)
            sector_w: dict[str, float] = {}
            country_w: dict[str, float] = {}
            cap_w: dict[str, float] = {"large": 0.0, "mid": 0.0, "small": 0.0}
            for pos in p.positions:
                q = await get_market_provider().get_quote(pos.ticker)
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

    svc = MarketDashboardService()
    return await svc.build(
        watchlist=watchlist,
        alerts=alerts,
        portfolio_slice=portfolio_slice,
        opportunities=opportunities,
        risks=risks,
        recently_analyzed=recently,
        provider_health=provider_health,
    )


@router.get("/dashboard/watchlist-matrix", response_model=list[WatchlistMatrixRow])
async def get_watchlist_matrix(
    session: AsyncSession = Depends(get_session),
) -> list[WatchlistMatrixRow]:
    watchlist = await WatchlistRepository(session).list_active()
    tickers = [w.ticker for w in watchlist]
    memory = await InvestmentMemoryRepository(session).latest_by_ticker(tickers)
    return await WatchlistMatrixService(get_market_provider()).build(watchlist, memory)
