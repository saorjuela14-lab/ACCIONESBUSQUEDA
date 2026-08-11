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
        self._equity_cache: float | None = None

    async def _book_equity(self) -> float | None:
        if self._equity_cache is not None:
            return self._equity_cache
        if not self._broker.is_configured():
            return None
        try:
            acct = await self._broker.get_account()
            eq = float(acct.equity or acct.portfolio_value or 0.0)
            self._equity_cache = eq if eq > 0 else None
        except Exception:
            self._equity_cache = None
        return self._equity_cache

    async def _exit_params(self) -> dict[str, float | int | None]:
        """Institutional defaults, or faster micro rotation when equity is tiny."""
        s = self._settings
        equity = await self._book_equity()
        micro = equity is not None and 0 < equity <= float(s.lifecycle_micro_equity_usd)
        if micro:
            return {
                "trailing_pct": s.lifecycle_micro_trailing_pct or None,
                "time_stop_days": s.lifecycle_micro_time_stop_days or None,
                "stop_pct": s.lifecycle_micro_default_stop_pct,
                "target_pct": s.lifecycle_micro_default_target_pct,
                "micro": True,
            }
        return {
            "trailing_pct": s.lifecycle_trailing_pct or None,
            "time_stop_days": s.lifecycle_time_stop_days or None,
            "stop_pct": s.lifecycle_default_stop_pct,
            "target_pct": s.lifecycle_default_target_pct,
            "micro": False,
        }

    async def _sync_broker_stop(self, mandate: PositionMandate, stop: float | None) -> str | None:
        if not self._settings.lifecycle_sync_broker_stops:
            return None
        if not stop or stop <= 0 or mandate.qty <= 0:
            return None
        if not self._broker.is_configured():
            return None
        try:
            result = await self._broker.replace_protective_stop(
                symbol=mandate.symbol,
                qty=float(mandate.qty),
                stop_price=float(stop),
            )
            if result is None:
                return None
            if result.error:
                return f"broker stop fail: {result.error}"
            return f"broker GTC stop @{stop:.4f} id={result.id or '?'}"
        except Exception as exc:
            return f"broker stop sync error: {exc}"

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
        params = await self._exit_params()
        trail = params["trailing_pct"]
        days = params["time_stop_days"]
        stop_pct = float(params["stop_pct"] or 0)
        target_pct = float(params["target_pct"] or 0)
        if stop_loss is None and entry_price > 0 and stop_pct > 0:
            stop_loss = round(entry_price * (1 - stop_pct), 4)
        if take_profit is None and entry_price > 0 and target_pct > 0:
            take_profit = round(entry_price * (1 + target_pct), 4)
        mandate = PositionMandate(
            symbol=symbol.upper(),
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_pct=float(trail) if trail else None,
            peak_price=entry_price,
            time_stop_days=int(days) if days else None,
            thesis=thesis,
            sector=sector,
            beta=beta,
            status="open",
        )
        saved = await self._mandates.upsert_open(mandate)
        try:
            from services.trade_journal_service import TradeJournalService

            await TradeJournalService(self._session).record_open(
                symbol=saved.symbol,
                qty=saved.qty,
                entry_price=saved.entry_price,
                stop_loss=saved.stop_loss,
                take_profit=saved.take_profit,
                thesis=saved.thesis,
                mandate_id=saved.id,
                meta={"sector": saved.sector, "beta": saved.beta},
            )
        except Exception as exc:
            logger.warning("trade_journal.open_failed", symbol=saved.symbol, error=str(exc))
        # Ensure Alpaca has a live GTC stop even if entry bracket was day/expired
        detail = await self._sync_broker_stop(saved, saved.stop_loss)
        if detail:
            await self._audit.record(
                "protective_stop_sync",
                symbol=saved.symbol,
                message=detail,
                actor="lifecycle",
                payload={"stop": saved.stop_loss, "micro": params["micro"]},
            )
        return saved

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
                try:
                    from services.trade_journal_service import TradeJournalService

                    px = float(m.peak_price or m.entry_price or 0)
                    if px > 0:
                        await TradeJournalService(self._session).record_close(
                            symbol=sym,
                            exit_price=px,
                            exit_reason=m.exit_reason,
                            closed_at=m.closed_at,
                        )
                except Exception as exc:
                    logger.warning("trade_journal.close_failed", symbol=sym, error=str(exc))

        out: list[PositionMandate] = []
        params = await self._exit_params()
        for p in positions:
            sym = p.symbol.upper()
            if sym in existing:
                m = existing[sym]
                m.qty = float(p.qty)
                if p.current_price and (m.peak_price is None or p.current_price > m.peak_price):
                    m.peak_price = float(p.current_price)
                # Keep open mandates on the active policy (micro vs institutional)
                if params["trailing_pct"] and (
                    m.trailing_pct is None or abs(float(m.trailing_pct) - float(params["trailing_pct"])) > 1e-9
                ):
                    m.trailing_pct = float(params["trailing_pct"])
                if params["time_stop_days"] and m.time_stop_days != int(params["time_stop_days"]):
                    m.time_stop_days = int(params["time_stop_days"])
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

        entry = float(mandate.entry_price or 0)
        arm_pct = float(self._settings.lifecycle_trail_arm_profit_pct or 0)
        # Turtle/Livermore: do not trail from entry noise — arm only after real profit
        trail_armed = entry > 0 and peak >= entry * (1.0 + max(0.0, arm_pct))

        # Trailing stop from peak (only once armed)
        trail_stop = None
        if trail_armed and mandate.trailing_pct and peak > 0:
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

        # Prefer take-profit over calendar stops — strategy aims to bank gains
        if mandate.take_profit and price >= mandate.take_profit:
            return LifecycleAction(
                symbol=mandate.symbol,
                action="exit",
                reason=f"Take-profit @ {price:.4f} ≥ {mandate.take_profit:.4f}",
                new_stop=effective_stop,
            )

        if effective_stop and price <= effective_stop:
            return LifecycleAction(
                symbol=mandate.symbol,
                action="exit",
                reason=f"Stop/trailing tocado @ {price:.4f} ≤ {effective_stop:.4f}",
                new_stop=effective_stop,
            )

        # Time-stop is last resort: only when underwater / flat (never cut winners by calendar)
        if mandate.time_stop_days and mandate.opened_at:
            opened = mandate.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_days = (now - opened).total_seconds() / 86400.0
            underwater = entry <= 0 or price <= entry * 0.995
            if age_days >= mandate.time_stop_days and underwater:
                return LifecycleAction(
                    symbol=mandate.symbol,
                    action="exit",
                    reason=(
                        f"Time-stop último recurso {mandate.time_stop_days}d "
                        f"({age_days:.1f}d) · sin avance / en pérdida"
                    ),
                    new_stop=effective_stop,
                )

        if (
            trail_armed
            and trail_stop
            and mandate.stop_loss
            and trail_stop > (mandate.stop_loss or 0)
        ):
            return LifecycleAction(
                symbol=mandate.symbol,
                action="tighten_stop",
                reason=f"Trailing armado (+{arm_pct*100:.0f}%) sube stop a {trail_stop:.4f}",
                new_stop=trail_stop,
            )

        return LifecycleAction(symbol=mandate.symbol, action="hold", reason="OK", new_stop=effective_stop)

    async def _record_stop_cooldown(self, symbol: str, reason: str, price: float) -> None:
        """Block revenge re-entries (investor discipline / prop-desk cool-off)."""
        mins = int(self._settings.auto_execute_post_stop_cooldown_minutes or 0)
        if mins <= 0:
            return
        from database.repositories.ops_repository import OpsFlagRepository

        await OpsFlagRepository(self._session).set_json(
            "post_stop_cooldown",
            {
                "symbol": symbol.upper(),
                "reason": reason[:240],
                "price": price,
                "until": (utc_now().timestamp() + mins * 60),
                "minutes": mins,
                "set_at": utc_now().isoformat(),
            },
        )

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
                broker_detail = await self._sync_broker_stop(m, decision.new_stop)
                await self._audit.record(
                    "trailing_update",
                    symbol=m.symbol,
                    message=decision.reason
                    + (f" · {broker_detail}" if broker_detail else ""),
                    actor="lifecycle",
                    payload={
                        "stop": decision.new_stop,
                        "price": price,
                        "broker": broker_detail,
                    },
                )
                decision.executed = True
                if broker_detail:
                    decision.detail = broker_detail
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
                if executed:
                    try:
                        from services.trade_journal_service import TradeJournalService

                        await TradeJournalService(self._session).record_close(
                            symbol=m.symbol,
                            exit_price=price,
                            exit_reason=decision.reason,
                            closed_at=now,
                        )
                    except Exception as exc:
                        logger.warning(
                            "trade_journal.close_failed", symbol=m.symbol, error=str(exc)
                        )
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
                # Broker may have already filled the protective stop (403 qty=0) — still cool down
                already_flat = "insufficient qty" in (detail or "").lower()
                if executed or already_flat:
                    exits.append(m.symbol)
                    protective = any(
                        k in (decision.reason or "")
                        for k in ("Stop", "trailing", "Tesis invalidada", "Time-stop", "perdida")
                    )
                    if protective and "Take-profit" not in (decision.reason or ""):
                        await self._record_stop_cooldown(m.symbol, decision.reason, price)
            else:
                await self._mandates.save(m)

            actions.append(decision)

        return LifecycleScanReport(
            positions=len(mandates),
            actions=actions,
            exits=exits,
            warnings=warnings,
        )
