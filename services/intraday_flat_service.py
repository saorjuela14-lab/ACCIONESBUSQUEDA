"""Smart EOD — protect the stop; let 2R run; carry recovery reds.

CEO override 2026-09-18: harvesting +0.3–0.8% greens destroyed the +12% mark.
Policy:
- Continuous holdings review still runs every autopilot cycle (reformulate / TP / trail).
- Near regular close: do **not** flatten noise greens. Carry toward take-profit 16%.
- Red positions stay open overnight (recovery) unless stop / ≤−8% / tesis sell.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

from config.settings import get_settings
from database.repositories.ops_repository import PositionMandateRepository
from domain.ops import utc_now
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger
from utils.market_hours import US_EASTERN, in_eod_flat_window, now_et

logger = get_logger(__name__)

_USD_PARKING = frozenset({"USDTUSD", "USDT/USD", "USDCUSD", "USDC/USD"})


def _is_parking(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return sym in _USD_PARKING or sym.replace("/", "") in {"USDTUSD", "USDCUSD"}


class IntradayFlatService:
    """Selective end-of-day flatten that protects profits without locking in avoidable losses."""

    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
    ) -> None:
        self._session = session
        self._broker = broker or AlpacaOrderService()
        self._mandates = PositionMandateRepository(session)
        self._audit = AuditService(session)
        self._settings = get_settings()

    def enabled(self) -> bool:
        return bool(self._settings.intraday_only_enabled)

    def buys_blocked_now(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, "intraday_only_off"
        mins = float(self._settings.intraday_flat_minutes_before_close)
        if in_eod_flat_window(mins):
            return True, f"eod_flat_window_{int(mins)}m"
        return False, "ok"

    def should_flat_now(self) -> tuple[bool, str]:
        if not self.enabled():
            return False, "intraday_only_off"
        mins = float(self._settings.intraday_flat_minutes_before_close)
        if in_eod_flat_window(mins):
            return True, f"eod_flat_window_{int(mins)}m"
        return False, "outside_window"

    def _pnl_pct(self, pos: Any) -> float:
        entry = float(getattr(pos, "avg_entry_price", 0) or 0)
        px = float(getattr(pos, "current_price", 0) or 0)
        if entry > 0 and px > 0:
            return (px / entry - 1.0) * 100.0
        # Fallback to broker unrealized %
        plpc = getattr(pos, "unrealized_plpc", None)
        if plpc is not None:
            return float(plpc) * 100.0
        return 0.0

    def _classify(
        self,
        pos: Any,
        mandate: Any | None,
    ) -> tuple[str, str]:
        """Return (action, reason) where action is close|carry."""
        pnl = self._pnl_pct(pos)
        max_carry_loss = float(self._settings.intraday_carry_max_loss_pct)
        let_2r = bool(getattr(self._settings, "intraday_2r_hold_enabled", True))
        min_green = float(self._settings.intraday_flat_min_pnl_pct)
        trail_arm = float(getattr(self._settings, "lifecycle_trail_arm_profit_pct", 0.05) or 0.05)
        bank_pct = trail_arm * 100.0  # +5% default — never bank noise ticks

        if mandate is not None and getattr(mandate, "thesis_invalidated", False):
            return "close", f"tesis_invalidada:{pnl:+.2f}%"

        # Hard cut: too deep underwater — do not hope forever
        if pnl <= -abs(max_carry_loss):
            return "close", f"perdida_max_carry:{pnl:+.2f}%<=-{abs(max_carry_loss):.1f}%"

        # Also respect mandate stop if already breached
        stop = getattr(mandate, "stop_loss", None) if mandate else None
        tp = getattr(mandate, "take_profit", None) if mandate else None
        px = float(getattr(pos, "current_price", 0) or 0)
        if stop and px > 0 and px <= float(stop):
            return "close", f"stop_tocado:@{px:.4f}<={float(stop):.4f}"
        if tp and px > 0 and float(tp) > 0 and px >= float(tp) * 0.995:
            return "close", f"take_profit:@{px:.4f}≥{float(tp):.4f}"

        if let_2r:
            if pnl < 0:
                return "carry", f"recuperacion_carry:{pnl:+.2f}%"
            return "carry", f"hacia_2R_hold:{pnl:+.2f}%"

        winners_only = bool(self._settings.intraday_flat_winners_only)
        if winners_only:
            # Legacy harvest: only bank once trail would arm (≥5%), never +0.5% noise
            if pnl >= max(min_green, bank_pct):
                return "close", f"asegurar_ganancia:{pnl:+.2f}%"
            if pnl >= 0:
                return "carry", f"hacia_2R_hold:{pnl:+.2f}%"
            return "carry", f"en_rojo_carry_overnight:{pnl:+.2f}%"

        # Legacy blunt flat
        return "close", f"flat_total:{pnl:+.2f}%"

    async def run(
        self,
        *,
        force: bool = False,
        reason: str | None = None,
        actor: str = "intraday_flat",
        close_all: bool = False,
    ) -> dict[str, Any]:
        if not self.enabled() and not force:
            return {"skipped": True, "reason": "intraday_only_off"}

        why = reason
        if not why:
            should, tag = self.should_flat_now()
            if not should and not force:
                return {"skipped": True, "reason": tag}
            why = tag if should else "manual"

        if not self._broker.is_configured():
            return {"skipped": True, "reason": "broker_unconfigured"}

        try:
            positions = await self._broker.get_positions()
        except Exception as exc:
            return {"error": str(exc)}

        equity_pos = [p for p in positions if not _is_parking(p.symbol)]
        if not equity_pos:
            return {
                "flattened": True,
                "closed": [],
                "carried": [],
                "reason": why,
                "message": "already_flat",
            }

        open_m = {m.symbol: m for m in await self._mandates.list_open()}
        to_close: list[tuple[Any, str]] = []
        carried: list[dict[str, Any]] = []

        for pos in equity_pos:
            sym = (pos.symbol or "").upper()
            mandate = open_m.get(sym)
            if close_all or force and reason == "manual_api_force_all":
                action, detail = "close", "force_all"
            else:
                action, detail = self._classify(pos, mandate)
            pnl = round(self._pnl_pct(pos), 2)
            if action == "close":
                to_close.append((pos, detail))
            else:
                carried.append(
                    {
                        "symbol": sym,
                        "qty": pos.qty,
                        "pnl_pct": pnl,
                        "reason": detail,
                    }
                )

        closed: list[dict[str, Any]] = []
        errors: list[str] = []

        # Cancel open orders only for names we are exiting (keep stops on carried)
        if to_close:
            try:
                open_orders = await self._broker.list_orders(status="open", limit=100)
            except Exception:
                open_orders = []
            close_syms = {(p.symbol or "").upper() for p, _ in to_close}
            for od in open_orders:
                if (od.symbol or "").upper() in close_syms and od.id:
                    try:
                        await self._broker.cancel_order(od.id)
                    except Exception:
                        pass

        for pos, detail in to_close:
            sym = (pos.symbol or "").upper().replace("/", "")
            try:
                raw = await self._broker.close_position(sym)
                closed.append(
                    {
                        "symbol": sym,
                        "qty": pos.qty,
                        "market_value": pos.market_value,
                        "pnl_pct": round(self._pnl_pct(pos), 2),
                        "detail": detail,
                        "status": (raw or {}).get("status") if isinstance(raw, dict) else "submitted",
                        "order_id": (raw or {}).get("id") if isinstance(raw, dict) else None,
                    }
                )
                m = open_m.get(sym) or await self._mandates.get_open(sym)
                if m:
                    m.status = "closed"
                    m.closed_at = utc_now()
                    m.exit_reason = f"EOD smart flat: {detail} ({why})"
                    await self._mandates.save(m)
                    try:
                        from services.trade_journal_service import TradeJournalService

                        exit_px = float(pos.current_price or pos.avg_entry_price or m.entry_price or 0)
                        if exit_px > 0:
                            await TradeJournalService(self._session).record_close(
                                symbol=sym,
                                exit_price=exit_px,
                                exit_reason=m.exit_reason,
                                closed_at=m.closed_at,
                                fill_entry_price=float(pos.avg_entry_price or 0) or None,
                            )
                    except Exception as jexc:
                        logger.warning("trade_journal.close_failed", symbol=sym, error=str(jexc))
            except Exception as exc:
                errors.append(f"{sym}: {exc}")

        # Annotate carried mandates for next-session fishing
        for c in carried:
            m = open_m.get(c["symbol"])
            if m:
                note = f"[carry overnight {c['pnl_pct']:+.2f}% · {c['reason']}]"
                prev = (m.thesis or "").strip()
                if "[carry overnight" not in prev:
                    m.thesis = f"{note} {prev}".strip()[:400]
                    m.last_checked_at = utc_now()
                    await self._mandates.save(m)

        msg = (
            f"EOD 2R hold ({why}): cerradas={len(closed)} "
            f"carry={len(carried)}"
            + (f" errores={len(errors)}" if errors else "")
        )
        await self._audit.record(
            "intraday_flat",
            actor=actor,
            success=not errors,
            message=msg,
            paper=self._broker.paper if self._broker.is_configured() else None,
            payload={
                "closed": closed,
                "carried": carried,
                "errors": errors,
                "reason": why,
                "winners_only": bool(self._settings.intraday_flat_winners_only),
                "two_r_hold": bool(getattr(self._settings, "intraday_2r_hold_enabled", True)),
            },
        )
        logger.info(
            "intraday.smart_flat",
            reason=why,
            closed=len(closed),
            carried=len(carried),
            errors=len(errors),
        )
        return {
            "flattened": len(to_close) > 0 and not carried,
            "partial": bool(closed) and bool(carried),
            "reason": why,
            "closed": closed,
            "carried": carried,
            "errors": errors,
            "message": msg,
        }
