"""Continuous Alpaca ↔ NexBuy portfolio reconciliation."""

from __future__ import annotations

from domain.entities import PortfolioPosition
from domain.ops import ReconcileDiff, ReconcileReport
from database.repositories.portfolio_repository import PortfolioRepository
from providers.market.factory import get_market_provider
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from services.portfolio_service import PortfolioService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


class ReconcileService:
    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
    ) -> None:
        self._session = session
        self._broker = broker or AlpacaOrderService()
        self._portfolios = PortfolioService(PortfolioRepository(session), get_market_provider())
        self._audit = AuditService(session)

    async def reconcile(self, *, sync: bool = True, portfolio_id: str | None = None) -> ReconcileReport:
        if not self._broker.is_configured():
            return ReconcileReport(message="Alpaca no configurada", synced=False)

        account = await self._broker.get_account()
        broker_positions = await self._broker.get_positions()

        portfolios = await self._portfolios.list_all()
        if not portfolios:
            # Create via bootstrap path if missing
            from services.portfolio_bootstrap_service import PortfolioBootstrapService

            boot = PortfolioBootstrapService(self._portfolios, self._broker)
            pf = await boot.sync_from_alpaca()
            await self._audit.record(
                "reconcile",
                message=f"Portafolio creado/sincronizado desde Alpaca id={pf.id}",
                paper=self._broker.paper,
                payload={"portfolio_id": pf.id},
            )
            return ReconcileReport(
                portfolio_id=pf.id,
                alpaca_positions=len(broker_positions),
                nexbuy_positions=len(pf.positions),
                cash_alpaca=account.cash,
                cash_nexbuy=pf.cash,
                synced=True,
                message="Portafolio NexBuy creado desde Alpaca",
            )

        pf = next((p for p in portfolios if p.id == portfolio_id), None) if portfolio_id else None
        if pf is None:
            pf = sorted(portfolios, key=lambda x: x.updated_at, reverse=True)[0]

        alpaca_map = {p.symbol.upper(): p for p in broker_positions}
        nexbuy_map = {p.ticker.upper(): p for p in pf.positions}

        diffs: list[ReconcileDiff] = []
        all_symbols = set(alpaca_map) | set(nexbuy_map)
        for sym in sorted(all_symbols):
            a = alpaca_map.get(sym)
            n = nexbuy_map.get(sym)
            if a and not n:
                diffs.append(ReconcileDiff(symbol=sym, field="presence", alpaca="open", nexbuy="missing"))
            elif n and not a:
                diffs.append(ReconcileDiff(symbol=sym, field="presence", alpaca="missing", nexbuy="open"))
            elif a and n:
                if abs(float(a.qty) - float(n.shares)) > 1e-6:
                    diffs.append(
                        ReconcileDiff(symbol=sym, field="qty", alpaca=a.qty, nexbuy=n.shares)
                    )
                if n.average_cost and abs(float(a.avg_entry_price) - float(n.average_cost)) > 0.02:
                    diffs.append(
                        ReconcileDiff(
                            symbol=sym,
                            field="avg_price",
                            alpaca=a.avg_entry_price,
                            nexbuy=n.average_cost,
                        )
                    )

        cash_diff = abs((account.cash or 0) - (pf.cash or 0))
        if cash_diff > 0.05:
            diffs.append(
                ReconcileDiff(symbol="CASH", field="cash", alpaca=account.cash, nexbuy=pf.cash)
            )

        synced = False
        if sync:
            mirrored = [
                PortfolioPosition(
                    ticker=p.symbol.upper(),
                    shares=float(p.qty),
                    average_cost=float(p.avg_entry_price or 0),
                    current_price=float(p.current_price or 0) or None,
                )
                for p in broker_positions
            ]
            equity = account.equity or account.portfolio_value or account.cash
            updated = await self._portfolios.mirror_positions(
                pf.id,
                mirrored,
                cash=float(account.cash or 0),
                initial_capital=float(equity or pf.initial_capital),
            )
            pf = updated
            synced = True

        msg = (
            f"Reconciliación {'aplicada' if synced else 'solo diff'}: "
            f"{len(diffs)} diferencias · Alpaca pos={len(broker_positions)} NexBuy={len(pf.positions)}"
        )
        await self._audit.record(
            "reconcile",
            message=msg,
            paper=self._broker.paper,
            success=True,
            payload={
                "diffs": [d.model_dump(mode="json") for d in diffs[:40]],
                "synced": synced,
                "portfolio_id": pf.id,
            },
        )
        logger.info("reconcile.done", diffs=len(diffs), synced=synced, portfolio_id=pf.id)
        return ReconcileReport(
            portfolio_id=pf.id,
            alpaca_positions=len(broker_positions),
            nexbuy_positions=len(pf.positions),
            cash_alpaca=account.cash,
            cash_nexbuy=pf.cash,
            diffs=diffs,
            synced=synced,
            message=msg,
        )
