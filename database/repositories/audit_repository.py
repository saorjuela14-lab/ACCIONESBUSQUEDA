"""Audit event repository."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AuditEventORM, utc_now
from domain.ops import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> AuditEvent:
        eid = event.id or str(uuid4())
        row = AuditEventORM(
            id=eid,
            created_at=event.created_at or utc_now(),
            action=event.action,
            actor=event.actor,
            symbol=event.symbol,
            paper=event.paper,
            success=event.success,
            message=event.message or "",
            payload_json=json.dumps(event.payload or {}, default=str),
        )
        self._session.add(row)
        await self._session.commit()
        event.id = eid
        return event

    def _row_to_event(self, r: AuditEventORM) -> AuditEvent:
        try:
            payload = json.loads(r.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        return AuditEvent(
            id=r.id,
            created_at=r.created_at,
            action=r.action,
            actor=r.actor,
            symbol=r.symbol,
            paper=r.paper,
            success=r.success,
            message=r.message,
            payload=payload,
        )

    async def list_recent(self, limit: int = 50, action: str | None = None, offset: int = 0) -> list[AuditEvent]:
        q = select(AuditEventORM).order_by(AuditEventORM.created_at.desc()).offset(offset).limit(limit)
        if action:
            q = q.where(AuditEventORM.action == action)
        rows = (await self._session.execute(q)).scalars().all()
        return [self._row_to_event(r) for r in rows]

    async def list_recent_page(
        self, *, limit: int = 40, offset: int = 0, action: str | None = None
    ) -> tuple[list[AuditEvent], int]:
        count_q = select(func.count()).select_from(AuditEventORM)
        if action:
            count_q = count_q.where(AuditEventORM.action == action)
        total = int((await self._session.execute(count_q)).scalar() or 0)
        items = await self.list_recent(limit=limit, action=action, offset=offset)
        return items, total
