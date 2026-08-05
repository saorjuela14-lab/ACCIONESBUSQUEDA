"""Intraday-only policy — flatten equities before the regular close; no overnight risk."""

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
    """Closes open equity positions so the book does not carry overnight news risk."""

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

    async def _overnight_leftovers(self) -> list[str]:
        """Symbols held from a prior ET session (should never happen under this policy)."""
        if not self._broker.is_configured():
            return []
        today = now_et().date()
        leftover: list[str] = []
        try:
            positions = await self._broker.get_positions()
        except Exception:
            return []
        open_m = {m.symbol: m for m in await self._mandates.list_open()}
        for p in positions:
            sym = (p.symbol or "").upper()
            if _is_parking(sym):
                continue
            m = open_m.get(sym)
            opened = m.opened_at if m else None
            if opened is None:
                # Unknown age + equity held while market open → treat as overnight risk
                leftover.append(sym)
                continue
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            opened_et = opened.astimezone(US_EASTERN).date()
            if opened_et < today:
                leftover.append(sym)
        return leftover

    async def run(
        self,
        *,
        force: bool = False,
        reason: str | None = None,
        actor: str = "intraday_flat",
    ) -> dict[str, Any]:
        if not self.enabled() and not force:
            return {"skipped": True, "reason": "intraday_only_off"}

        why = reason
        if not why:
            should, tag = self.should_flat_now()
            leftovers = await self._overnight_leftovers()
            if leftovers:
                should = True
                tag = f"overnight_leftover:{','.join(leftovers)}"
            if not should and not force:
                return {"skipped": True, "reason": tag}
            why = tag

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
                "reason": why,
                "message": "already_flat",
            }

        closed: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            await self._broker.cancel_all_orders()
        except Exception as exc:
            errors.append(f"cancel_orders: {exc}")

        for pos in equity_pos:
            sym = (pos.symbol or "").upper().replace("/", "")
            try:
                raw = await self._broker.close_position(sym)
                closed.append(
                    {
                        "symbol": sym,
                        "qty": pos.qty,
                        "market_value": pos.market_value,
                        "status": (raw or {}).get("status") if isinstance(raw, dict) else "submitted",
                        "order_id": (raw or {}).get("id") if isinstance(raw, dict) else None,
                    }
                )
                m = await self._mandates.get_open(sym)
                if m:
                    m.status = "closed"
                    m.closed_at = utc_now()
                    m.exit_reason = f"Intraday flat: {why}"
                    await self._mandates.save(m)
            except Exception as exc:
                errors.append(f"{sym}: {exc}")

        msg = (
            f"Intraday flat ({why}): cerradas={len(closed)}"
            + (f" errores={len(errors)}" if errors else "")
        )
        await self._audit.record(
            "intraday_flat",
            actor=actor,
            success=not errors,
            message=msg,
            paper=self._broker.paper if self._broker.is_configured() else None,
            payload={"closed": closed, "errors": errors, "reason": why},
        )
        logger.info("intraday.flat", reason=why, closed=len(closed), errors=len(errors))
        return {
            "flattened": True,
            "reason": why,
            "closed": closed,
            "errors": errors,
            "message": msg,
        }
