"""Daily short-term trade recommendation API."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
from database.engine import get_session
from database.repositories.daily_trade_repository import DailyTradeRepository
from database.repositories.portfolio_repository import PortfolioRepository
from database.repositories.watchlist_repository import WatchlistRepository
from domain.daily_trade import DailyTradeReport
from domain.micro_portfolio import MicroAllocationLineOut, MicroPortfolioPlanOut
from models.schemas import DailyTradeGenerateRequest, MicroManageRequest
from providers.market.factory import get_market_provider
from services.analysis_factory import build_analysis_service
from services.company_discovery_service import CompanyDiscoveryService
from services.daily_trade_recommendation_service import DailyTradeRecommendationService
from services.desk_learning_service import DeskLearningService
from services.micro_portfolio_manager_service import MicroPortfolioManagerService

router = APIRouter()


def _build_service(session: AsyncSession) -> DailyTradeRecommendationService:
    market = get_market_provider()
    return DailyTradeRecommendationService(
        market_provider=market,
        discovery_service=CompanyDiscoveryService(market_provider=market),
        trade_repo=DailyTradeRepository(session),
        analysis_service=build_analysis_service(session),
    )


async def _portfolio_capital(session: AsyncSession, org_id: str | None) -> float | None:
    portfolios = await PortfolioRepository(session).list_all(org_id=org_id)
    if not portfolios:
        return None
    p = sorted(portfolios, key=lambda x: x.updated_at, reverse=True)[0]
    # Prefer live cash / total value over stale initial_capital (often the $1000 default)
    for candidate in (p.cash, getattr(p, "total_value", None), p.initial_capital):
        if candidate is not None and float(candidate) > 0:
            return float(candidate)
    return None


async def _resolve_manage_capital(
    session: AsyncSession,
    requested: float | None,
    *,
    is_desk: bool,
    org_id: str,
) -> float:
    """Prefer explicit request → (desk) Alpaca book → org portfolio. Never invent $1000."""
    if requested is not None and requested > 0:
        return float(requested)

    if is_desk:
        try:
            from services.alpaca_order_service import AlpacaOrderService

            alpaca = AlpacaOrderService()
            if alpaca.is_configured():
                account = await alpaca.get_account()
                for candidate in (
                    account.equity,
                    account.portfolio_value,
                    account.cash,
                    account.buying_power,
                ):
                    if candidate is not None and float(candidate) > 0:
                        return round(float(candidate), 2)
        except Exception:
            pass

    from_pf = await _portfolio_capital(session, org_id)
    if from_pf and from_pf > 0:
        return round(from_pf, 2)

    raise HTTPException(
        status_code=400,
        detail=(
            "No hay capital detectable. "
            + (
                "Conecta Alpaca, sincroniza el portafolio (Sincronizar desde Alpaca) "
                "o envía capital>0 en el body."
                if is_desk
                else "Crea un portafolio o envía capital>0 en el body."
            )
        ),
    )


@router.get("/recommendations/daily/latest", response_model=DailyTradeReport)
async def latest_daily_trades(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> DailyTradeReport:
    """Últimas recomendaciones diarias de corto plazo."""
    report = await _build_service(session).get_latest()
    if not report:
        capital = await _portfolio_capital(session, scope.book_org_id())
        report = await _build_service(session).generate(
            session="pre_market",
            persist=True,
            capital=capital,
            exclude_tickers=await DeskLearningService(session).avoid_tickers(),
        )
    return report


@router.post("/recommendations/daily/generate", response_model=DailyTradeReport)
async def generate_daily_trades(
    request: DailyTradeGenerateRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> DailyTradeReport:
    """Genera recomendaciones de corto plazo bajo demanda."""
    org = scope.book_org_id()
    watchlist = await WatchlistRepository(session).list_active(org_id=org)
    exclude = list(request.exclude_tickers or [])
    exclude.extend(w.ticker for w in watchlist)
    exclude = await DeskLearningService(session).merge_excludes(exclude)

    capital = request.capital
    if capital is None:
        capital = await _portfolio_capital(session, org)

    return await _build_service(session).generate(
        session=request.session,
        max_picks=request.max_picks,
        exclude_tickers=exclude,
        persist=True,
        capital=capital,
    )


async def _held_alpaca_tickers(*, is_desk: bool) -> list[str]:
    """Open Alpaca positions — desk only (shared broker book)."""
    if not is_desk:
        return []
    try:
        from services.alpaca_order_service import AlpacaOrderService

        alpaca = AlpacaOrderService()
        if not alpaca.is_configured():
            return []
        positions = await alpaca.get_positions()
        out: list[str] = []
        for p in positions or []:
            if hasattr(p, "symbol"):
                sym = getattr(p, "symbol", None)
            elif isinstance(p, dict):
                sym = p.get("symbol") or p.get("ticker")
            else:
                sym = None
            if sym:
                out.append(str(sym).upper())
        return out
    except Exception:
        return []


@router.post("/recommendations/manage-capital", response_model=MicroPortfolioPlanOut)
async def manage_micro_capital(
    request: MicroManageRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> MicroPortfolioPlanOut:
    """Escritorio de capital: investiga penny stocks y arma plan con acciones enteras."""
    org = scope.book_org_id()
    watchlist = await WatchlistRepository(session).list_active(org_id=org)
    exclude = list(request.exclude_tickers or [])
    exclude.extend(w.ticker for w in watchlist)
    exclude.extend(await _held_alpaca_tickers(is_desk=scope.is_desk))
    exclude = await DeskLearningService(session).merge_excludes(exclude)

    capital = await _resolve_manage_capital(
        session, request.capital, is_desk=scope.is_desk, org_id=org
    )

    market = get_market_provider()
    manager = MicroPortfolioManagerService(
        market,
        CompanyDiscoveryService(market_provider=market),
        analysis_service=build_analysis_service(session),
    )
    plan = await manager.manage(capital=capital, exclude_tickers=exclude)

    if request.persist_as_daily and plan.picks:
        report = DailyTradeReport(
            report_date=date.today(),
            generated_at=datetime.now(timezone.utc),
            session="capital_desk",
            market_regime="managed",
            summary=plan.summary,
            picks=plan.picks,
        )
        await DailyTradeRepository(session).save(report)

    return MicroPortfolioPlanOut(
        capital=plan.capital,
        cash_reserve_usd=plan.cash_reserve_usd,
        deployable_usd=plan.deployable_usd,
        max_share_price=plan.max_share_price,
        lines=[
            MicroAllocationLineOut(
                ticker=l.ticker,
                company_name=l.company_name,
                price=l.price,
                shares=l.shares,
                allocation_usd=l.allocation_usd,
                allocation_pct=l.allocation_pct,
                rationale=l.rationale,
                stop_loss=l.stop_loss,
                take_profit=l.take_profit,
            )
            for l in plan.lines
        ],
        picks=plan.picks,
        summary=plan.summary,
        warnings=plan.warnings,
    )


@router.get("/recommendations/daily/history", response_model=list[DailyTradeReport])
async def daily_trades_history(
    limit: int = 7,
    session: AsyncSession = Depends(get_session),
) -> list[DailyTradeReport]:
    """Historial reciente de recomendaciones diarias."""
    repo = DailyTradeRepository(session)
    reports = await repo.list_recent(limit=min(limit, 30))
    return reports
