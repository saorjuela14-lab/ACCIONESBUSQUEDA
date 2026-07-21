"""Operational kill switch — panic → cancel orders + flat positions."""

from __future__ import annotations

from domain.ops import KillSwitchState, utc_now
from database.repositories.ops_repository import OpsFlagRepository
from services.alpaca_order_service import AlpacaOrderService
from services.audit_service import AuditService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


class KillSwitchService:
    def __init__(
        self,
        session: AsyncSession,
        broker: AlpacaOrderService | None = None,
    ) -> None:
        self._flags = OpsFlagRepository(session)
        self._audit = AuditService(session)
        self._broker = broker or AlpacaOrderService()

    async def status(self) -> KillSwitchState:
        return await self._flags.get_kill_switch()

    async def is_active(self) -> bool:
        state = await self.status()
        return bool(state.active)

    async def activate(
        self,
        *,
        reason: str = "panic flat",
        actor: str = "user",
        flatten: bool = True,
        confirm: bool = False,
    ) -> KillSwitchState:
        if not confirm:
            raise ValueError("confirm=true requerido para activar kill switch")

        state = KillSwitchState(
            active=True,
            reason=reason,
            activated_at=utc_now(),
            activated_by=actor,
            flat_attempted=False,
        )

        flat_msg = "sin flatten"
        if flatten and self._broker.is_configured():
            state.flat_attempted = True
            parts: list[str] = []
            try:
                cancelled = await self._broker.cancel_all_orders()
                parts.append(f"canceladas={len(cancelled)}")
            except Exception as exc:
                parts.append(f"cancel_error={exc}")
            try:
                closed = await self._broker.close_all_positions(cancel_orders=True)
                parts.append(f"cerradas={len(closed)}")
            except Exception as exc:
                parts.append(f"close_error={exc}")
            flat_msg = "; ".join(parts)
            state.flat_result = flat_msg

        await self._flags.set_kill_switch(state)
        await self._audit.record(
            "kill_switch_on",
            actor=actor,
            message=f"{reason} · {flat_msg}",
            paper=self._broker.paper if self._broker.is_configured() else None,
            success=True,
            payload=state.model_dump(mode="json"),
        )
        logger.warning("kill_switch.activated", reason=reason, flat=flat_msg)
        return state

    async def deactivate(self, *, actor: str = "user", confirm: bool = False) -> KillSwitchState:
        if not confirm:
            raise ValueError("confirm=true requerido para desactivar kill switch")
        state = KillSwitchState(active=False, reason=None, activated_at=None, activated_by=None)
        await self._flags.set_kill_switch(state)
        await self._audit.record(
            "kill_switch_off",
            actor=actor,
            message="Kill switch desactivado — trading permitido de nuevo",
            success=True,
        )
        logger.info("kill_switch.deactivated", actor=actor)
        return state
