"""Decision audit trail — every risk/order/ops action."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.audit_repository import AuditRepository
from domain.ops import AuditEvent
from utils.logging import get_logger

logger = get_logger(__name__)


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def record(
        self,
        action: str,
        *,
        message: str = "",
        actor: str = "system",
        symbol: str | None = None,
        paper: bool | None = None,
        success: bool = True,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            action=action,
            actor=actor,
            symbol=symbol.upper() if symbol else None,
            paper=paper,
            success=success,
            message=message,
            payload=payload or {},
        )
        saved = await self._repo.append(event)
        logger.info(
            "audit.event",
            action=action,
            symbol=saved.symbol,
            success=success,
            actor=actor,
        )
        return saved

    async def recent(self, limit: int = 50, action: str | None = None) -> list[AuditEvent]:
        return await self._repo.list_recent(limit=limit, action=action)
