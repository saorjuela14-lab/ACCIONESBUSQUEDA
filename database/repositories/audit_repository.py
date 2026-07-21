"""Audit event repository."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
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

    async def list_recent(self, limit: int = 50, action: str | None = None) -> list[AuditEvent]:
        q = select(AuditEventORM).order_by(AuditEventORM.created_at.desc()).limit(limit)
        if action:
            q = q.where(AuditEventORM.action == action)
        rows = (await self._session.execute(q)).scalars().all()
        out: list[AuditEvent] = []
        for r in rows:
            try:
                payload = json.loads(r.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            out.append(
                AuditEvent(
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
            )
        return out
