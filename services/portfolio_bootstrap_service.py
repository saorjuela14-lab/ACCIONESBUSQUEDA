"""Bootstrap / restore NexBuy portfolios when SQLite is wiped on redeploy.

FastAPI Cloud (Hobby) uses ephemeral disk — `./data/nexbuy.db` is lost on
restart. This service recreates a REAL portfolio from Alpaca cash+positions
so the CEO panel stays usable after redeploys.
"""

from __future__ import annotations

from domain.entities import Portfolio, PortfolioPosition
from domain.enums import PortfolioMode, StrategyType
from domain.firm_capital import FIRM_RETURN_BASE_USD
from services.alpaca_order_service import AlpacaOrderService
from services.portfolio_service import PortfolioService
from utils.logging import get_logger

logger = get_logger(__name__)


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
        default_cash: float = FIRM_RETURN_BASE_USD,
    ) -> tuple[Portfolio, str]:
        """Return existing newest portfolio, or create from Alpaca / defaults.

        Returns (portfolio, source) where source is existing|alpaca|default.
        Company tenants pass org_id and allow_alpaca=False (no shared broker book).
        """
        existing = await self._portfolios.list_all(org_id=org_id)
        if existing:
            p = sorted(existing, key=lambda x: x.updated_at, reverse=True)[0]
            return p, "existing"

        if allow_alpaca and self._alpaca.is_configured():
            try:
                synced = await self.sync_from_alpaca(org_id=org_id)
                if synced:
                    return synced, "alpaca"
            except Exception as exc:
                logger.warning("portfolio.bootstrap.alpaca_failed", error=str(exc))

        created = await self._portfolios.create(
            name=default_name,
            strategy=StrategyType.GROWTH,
            initial_capital=default_cash,
            cash=default_cash,
            mode=PortfolioMode.REAL,
            org_id=org_id,
        )
        logger.info("portfolio.bootstrap.default", portfolio_id=created.id, org_id=org_id)
        return created, "default"

    async def sync_from_alpaca(self, org_id: str | None = None) -> Portfolio | None:
        """Create a NexBuy portfolio mirroring Alpaca account + positions."""
        account = await self._alpaca.get_account()
        broker_positions = await self._alpaca.get_positions()

        cash = float(account.cash or 0)
        # Return baseline is always $20 — never stamp Alpaca equity (~21.68) as initial.
        initial = FIRM_RETURN_BASE_USD
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
            positions=len(positions),
        )
        return portfolio
