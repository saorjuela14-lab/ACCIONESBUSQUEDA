"""Bootstrap / restore NexBuy portfolios when SQLite is wiped on redeploy.

FastAPI Cloud (Hobby) uses ephemeral disk — `./data/nexbuy.db` is lost on
restart. This service recreates a REAL portfolio from Alpaca cash+positions
so the CEO panel stays usable after redeploys.

`initial_capital` is the deposited Alpaca base (never a silent $20 stamp).
"""

from __future__ import annotations

from domain.entities import Portfolio, PortfolioPosition
from domain.enums import PortfolioMode, StrategyType
from services.alpaca_order_service import AlpacaOrderService
from services.deposited_capital_service import resolve_trading_base
from services.portfolio_service import PortfolioService
from utils.logging import get_logger

logger = get_logger(__name__)


async def stamp_initial_from_deposits(
    portfolios: PortfolioService,
    portfolio: Portfolio,
    *,
    org_id: str | None,
    equity: float | None = None,
) -> Portfolio:
    """Migrate monarch `initial_capital` to deposited base without touching cash/positions."""
    snap = await resolve_trading_base(equity=equity)
    if not snap.amount or snap.amount <= 0:
        return portfolio
    # Never lock fluctuating conservative equity into the DB stamp.
    if str(snap.source or "").startswith("conservative"):
        return portfolio
    current = float(portfolio.initial_capital or 0)
    if abs(current - snap.amount) < 0.005:
        return portfolio
    updated = await portfolios.mirror_positions(
        portfolio.id,
        positions=list(portfolio.positions),
        cash=float(portfolio.cash or 0),
        initial_capital=snap.amount,
        org_id=org_id,
    )
    logger.info(
        "initial_capital.migrated",
        portfolio_id=portfolio.id,
        old=current,
        new=snap.amount,
        source=snap.source,
    )
    return updated


class PortfolioBootstrapService:
    def __init__(
        self,
        portfolio_service: PortfolioService,
        alpaca: AlpacaOrderService | None = None,
    ) -> None:
        self._portfolios = portfolio_service
        self._alpaca = alpaca or AlpacaOrderService()

    async def ensure_portfolio(
        self,
        *,
        org_id: str | None = None,
        allow_alpaca: bool = True,
        default_name: str = "Portafolio CEO",
        default_cash: float | None = None,
    ) -> tuple[Portfolio, str]:
        """Return existing newest portfolio, or create from Alpaca / deposited base.

        Returns (portfolio, source) where source is existing|alpaca|default.
        Company tenants pass org_id and allow_alpaca=False (no shared broker book).
        Never invents a silent $20 book.
        """
        existing = await self._portfolios.list_all(org_id=org_id)
        if existing:
            p = sorted(existing, key=lambda x: x.updated_at, reverse=True)[0]
            if allow_alpaca:
                p = await stamp_initial_from_deposits(self._portfolios, p, org_id=org_id)
            return p, "existing"

        if allow_alpaca and self._alpaca.is_configured():
            try:
                synced = await self.sync_from_alpaca(org_id=org_id)
                if synced:
                    return synced, "alpaca"
            except Exception as exc:
                logger.warning("portfolio.bootstrap.alpaca_failed", error=str(exc))

        equity = None
        if allow_alpaca and self._alpaca.is_configured():
            try:
                acct = await self._alpaca.get_account()
                equity = float(acct.equity or acct.cash or 0)
            except Exception:
                equity = None
        snap = await resolve_trading_base(equity=equity)
        cash = float(default_cash) if default_cash and default_cash > 0 else None
        if cash is None:
            cash = float(snap.amount) if snap.amount and snap.amount > 0 else None
        if cash is None or cash <= 0:
            logger.error("portfolio.bootstrap.no_base", source=snap.source)
            created = await self._portfolios.create(
                name=default_name,
                strategy=StrategyType.GROWTH,
                initial_capital=0.0,
                cash=0.0,
                mode=PortfolioMode.REAL,
                org_id=org_id,
            )
            return created, "default"
        created = await self._portfolios.create(
            name=default_name,
            strategy=StrategyType.GROWTH,
            initial_capital=float(snap.amount or cash),
            cash=cash,
            mode=PortfolioMode.REAL,
            org_id=org_id,
        )
        logger.info(
            "portfolio.bootstrap.default",
            portfolio_id=created.id,
            org_id=org_id,
            initial_capital=created.initial_capital,
            source=snap.source,
        )
        return created, "default"

    async def sync_from_alpaca(self, org_id: str | None = None) -> Portfolio | None:
        """Create a NexBuy portfolio mirroring Alpaca account + positions."""
        account = await self._alpaca.get_account()
        broker_positions = await self._alpaca.get_positions()

        cash = float(account.cash or 0)
        equity = float(account.equity or account.portfolio_value or cash or 0)
        snap = await resolve_trading_base(equity=equity)
        if snap.amount and snap.amount > 0:
            initial = snap.amount
        elif equity > 0:
            initial = round(equity, 2)
            logger.warning("portfolio.bootstrap.conservative_equity", amount=initial)
        else:
            logger.error("portfolio.bootstrap.sync_no_base")
            return None
        positions: list[PortfolioPosition] = []
        for pos in broker_positions:
            qty = float(pos.qty or 0)
            if qty <= 0:
                continue
            avg = float(pos.avg_entry_price or pos.current_price or 0)
            px = float(pos.current_price or avg or 0)
            if avg <= 0:
                continue
            positions.append(
                PortfolioPosition(
                    ticker=pos.symbol.upper(),
                    shares=qty,
                    average_cost=avg,
                    current_price=px or None,
                )
            )

        portfolio = await self._portfolios.create(
            name="Alpaca LIVE",
            strategy=StrategyType.GROWTH,
            initial_capital=initial,
            cash=round(cash, 2),
            mode=PortfolioMode.REAL,
            org_id=org_id or "monarch",
        )
        portfolio = await self._portfolios.mirror_positions(
            portfolio.id,
            positions=positions,
            cash=round(cash, 2),
            initial_capital=initial,
            org_id=org_id or "monarch",
        )
        logger.info(
            "portfolio.bootstrap.alpaca",
            portfolio_id=portfolio.id,
            cash=cash,
            initial_capital=initial,
            source=snap.source,
            positions=len(positions),
        )
        return portfolio
