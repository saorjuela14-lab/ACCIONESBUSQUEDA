"""Position lifecycle desk — trailing stops, time-stops, thesis invalidation → exit."""

from __future__ import annotations

from datetime import datetime, timezone

from config.settings import get_settings
from database.repositories.ops_repository import PositionMandateRepository
from domain.ops import LifecycleAction, LifecycleScanReport, PositionMandate, utc_now
from providers.market.factory import get_market_provider
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


class PositionLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
    ) -> None:
        self._session = session
        self._mandates = PositionMandateRepository(session)
        self._broker = broker or AlpacaOrderService()
        self._market = get_market_provider()
        self._audit = AuditService(session)
        self._settings = get_settings()

    async def register_from_fill(
        self,
        *,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        thesis: str | None = None,
        sector: str | None = None,
        beta: float | None = None,
    ) -> PositionMandate:
        s = self._settings
        trail = s.lifecycle_trailing_pct if s.lifecycle_trailing_pct > 0 else None
        days = s.lifecycle_time_stop_days if s.lifecycle_time_stop_days > 0 else None
        if stop_loss is None and entry_price > 0:
            stop_loss = round(entry_price * (1 - s.lifecycle_default_stop_pct), 4)
        if take_profit is None and entry_price > 0:
            take_profit = round(entry_price * (1 + s.lifecycle_default_target_pct), 4)
        mandate = PositionMandate(
            symbol=symbol.upper(),
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_pct=trail,
            peak_price=entry_price,
            time_stop_days=days,
            thesis=thesis,
            sector=sector,
            beta=beta,
            status="open",
        )
        return await self._mandates.upsert_open(mandate)

    async def invalidate_thesis(self, symbol: str, reason: str) -> PositionMandate | None:
        m = await self._mandates.get_open(symbol)
        if not m:
            return None
        m.thesis_invalidated = True
        m.invalidate_reason = reason
        m.last_checked_at = utc_now()
        await self._mandates.save(m)
        await self._audit.record(
            "thesis_invalidate",
            symbol=symbol,
            message=reason,
            actor="lifecycle",
        )
        return m

    async def sync_mandates_from_broker(self) -> list[PositionMandate]:
        """Ensure open broker positions have mandates (defaults)."""
        if not self._broker.is_configured():
            return await self._mandates.list_open()
        positions = await self._broker.get_positions()
        open_syms = {p.symbol.upper() for p in positions}
        existing = {m.symbol: m for m in await self._mandates.list_open()}

        # Close mandates no longer held
        for sym, m in existing.items():
            if sym not in open_syms:
                m.status = "closed"
                m.closed_at = utc_now()
                m.exit_reason = m.exit_reason or "posición ausente en Alpaca"
                await self._mandates.save(m)

        out: list[PositionMandate] = []
        for p in positions:
            sym = p.symbol.upper()
            if sym in existing:
                m = existing[sym]
                m.qty = float(p.qty)
                if p.current_price and (m.peak_price is None or p.current_price > m.peak_price):
                    m.peak_price = float(p.current_price)
                await self._mandates.save(m)
                out.append(m)
            else:
                out.append(
                    await self.register_from_fill(
                        symbol=sym,
                        qty=float(p.qty),
                        entry_price=float(p.avg_entry_price or p.current_price or 0),
                    )
                )
        return out

    def _evaluate(self, mandate: PositionMandate, price: float, now: datetime) -> LifecycleAction:
        peak = mandate.peak_price or mandate.entry_price or price
        if price > peak:
            peak = price

        # Trailing stop from peak
        trail_stop = None
        if mandate.trailing_pct and peak > 0:
            trail_stop = peak * (1 - mandate.trailing_pct)

        effective_stop = mandate.stop_loss
        if trail_stop is not None:
            if effective_stop is None or trail_stop > effective_stop:
                effective_stop = trail_stop

        if mandate.thesis_invalidated:
            return LifecycleAction(
                symbol=mandate.symbol,
                action="exit",
                reason=f"Tesis invalidada: {mandate.invalidate_reason or 'sin detalle'}",
                new_stop=effective_stop,
            )

        if mandate.time_stop_days and mandate.opened_at:
            opened = mandate.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_days = (now - opened).total_seconds() / 86400.0
            if age_days >= mandate.time_stop_days:
                return LifecycleAction(
                    symbol=mandate.symbol,
                    action="exit",
                    reason=f"Time-stop {mandate.time_stop_days}d alcanzado ({age_days:.1f}d)",
                    new_stop=effective_stop,
                )

        if effective_stop and price <= effective_stop:
            return LifecycleAction(
                symbol=mandate.symbol,
                action="exit",
                reason=f"Stop/trailing tocado @ {price:.4f} ≤ {effective_stop:.4f}",
                new_stop=effective_stop,
            )

        if mandate.take_profit and price >= mandate.take_profit:
            return LifecycleAction(
                symbol=mandate.symbol,
                action="exit",
                reason=f"Take-profit @ {price:.4f} ≥ {mandate.take_profit:.4f}",
                new_stop=effective_stop,
            )

        if trail_stop and mandate.stop_loss and trail_stop > (mandate.stop_loss or 0):
            return LifecycleAction(
                symbol=mandate.symbol,
                action="tighten_stop",
                reason=f"Trailing sube stop a {trail_stop:.4f}",
                new_stop=trail_stop,
            )

        return LifecycleAction(symbol=mandate.symbol, action="hold", reason="OK", new_stop=effective_stop)

    async def scan(self, *, execute_exits: bool = True) -> LifecycleScanReport:
        now = utc_now()
        warnings: list[str] = []
        actions: list[LifecycleAction] = []
        exits: list[str] = []

        try:
            mandates = await self.sync_mandates_from_broker()
        except Exception as exc:
            warnings.append(f"sync broker falló: {exc}")
            mandates = await self._mandates.list_open()

        for m in mandates:
            try:
                quote = await self._market.get_quote(m.symbol)
                price = float(quote.get("current_price") or m.peak_price or m.entry_price or 0)
            except Exception:
                price = float(m.peak_price or m.entry_price or 0)
            if price <= 0:
                warnings.append(f"{m.symbol}: sin precio")
                continue

            if m.peak_price is None or price > m.peak_price:
                m.peak_price = price

            decision = self._evaluate(m, price, now)
            m.last_checked_at = now

            if decision.action == "tighten_stop" and decision.new_stop:
                m.stop_loss = decision.new_stop
                await self._mandates.save(m)
                await self._audit.record(
                    "trailing_update",
                    symbol=m.symbol,
                    message=decision.reason,
                    actor="lifecycle",
                    payload={"stop": decision.new_stop, "price": price},
                )
                decision.executed = True
            elif decision.action == "exit" and execute_exits:
                executed = False
                detail = None
                if self._broker.is_configured():
                    try:
                        raw = await self._broker.close_position(m.symbol)
                        executed = True
                        detail = str(raw.get("id") or raw.get("status") or "closed")
                    except Exception as exc:
                        detail = str(exc)
                        warnings.append(f"{m.symbol}: exit falló ({exc})")
                else:
                    detail = "broker no configurado"
                m.status = "closed" if executed else "exiting"
                m.exit_reason = decision.reason
                m.closed_at = now if executed else None
                await self._mandates.save(m)
                await self._audit.record(
                    "lifecycle_exit",
                    symbol=m.symbol,
                    message=decision.reason,
                    actor="lifecycle",
                    success=executed,
                    paper=self._broker.paper if self._broker.is_configured() else None,
                    payload={"detail": detail, "price": price},
                )
                decision.executed = executed
                decision.detail = detail
                if executed:
                    exits.append(m.symbol)
            else:
                await self._mandates.save(m)

            actions.append(decision)

        return LifecycleScanReport(
            positions=len(mandates),
            actions=actions,
            exits=exits,
            warnings=warnings,
        )
