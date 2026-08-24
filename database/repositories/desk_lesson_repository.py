"""Daily desk lessons — durable avoid-list and error notes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeskLessonORM, utc_now


class DeskLessonRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _aware(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def upsert_avoid(
        self,
        *,
        ticker: str,
        error_tag: str,
        reason: str,
        expires_at: datetime,
        recommendation: str | None = None,
        actual_return_pct: float | None = None,
        memory_id: str | None = None,
        payload: dict | None = None,
    ) -> DeskLessonORM:
        t = ticker.upper().strip()
        now = utc_now()
        existing = (
            await self._session.execute(
                select(DeskLessonORM)
                .where(
                    DeskLessonORM.lesson_type == "avoid_ticker",
                    DeskLessonORM.ticker == t,
                    DeskLessonORM.expires_at > now,
                )
                .order_by(DeskLessonORM.created_at.desc())
            )
        ).scalars().first()
        if existing:
            existing.expires_at = expires_at
            existing.error_tag = error_tag
            existing.reason = reason
            existing.recommendation = recommendation
            existing.actual_return_pct = actual_return_pct
            existing.memory_id = memory_id or existing.memory_id
            existing.payload_json = json.dumps(payload or {}, default=str)
            await self._session.commit()
            return existing

        row = DeskLessonORM(
            id=str(uuid4()),
            created_at=now,
            expires_at=expires_at,
            lesson_type="avoid_ticker",
            ticker=t,
            error_tag=error_tag,
            reason=reason,
            recommendation=recommendation,
            actual_return_pct=actual_return_pct,
            memory_id=memory_id,
            payload_json=json.dumps(payload or {}, default=str),
        )
        self._session.add(row)
        await self._session.commit()
        return row

    async def add_note(
        self,
        *,
        reason: str,
        expires_at: datetime,
        ticker: str | None = None,
        agent_name: str | None = None,
        error_tag: str | None = None,
        payload: dict | None = None,
    ) -> DeskLessonORM:
        row = DeskLessonORM(
            id=str(uuid4()),
            created_at=utc_now(),
            expires_at=expires_at,
            lesson_type="note",
            ticker=(ticker or "").upper().strip() or None,
            agent_name=agent_name,
            error_tag=error_tag,
            reason=reason,
            payload_json=json.dumps(payload or {}, default=str),
        )
        self._session.add(row)
        await self._session.commit()
        return row

    async def list_active(self, *, lesson_type: str | None = None) -> list[DeskLessonORM]:
        now = utc_now()
        stmt = select(DeskLessonORM).where(DeskLessonORM.expires_at > now)
        if lesson_type:
            stmt = stmt.where(DeskLessonORM.lesson_type == lesson_type)
        stmt = stmt.order_by(DeskLessonORM.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def avoid_tickers(self) -> list[str]:
        rows = await self.list_active(lesson_type="avoid_ticker")
        seen: set[str] = set()
        out: list[str] = []
        for r in rows:
            t = (r.ticker or "").upper().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    async def latest_for_ticker(self, ticker: str) -> DeskLessonORM | None:
        t = ticker.upper().strip()
        now = utc_now()
        return (
            await self._session.execute(
                select(DeskLessonORM)
                .where(
                    DeskLessonORM.ticker == t,
                    DeskLessonORM.expires_at > now,
                )
                .order_by(DeskLessonORM.created_at.desc())
            )
        ).scalars().first()
