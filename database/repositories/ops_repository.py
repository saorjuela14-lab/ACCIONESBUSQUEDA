"""Ops flags + position mandate persistence."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import OpsFlagORM, PositionMandateORM, utc_now
from domain.ops import KillSwitchState, PositionMandate


class OpsFlagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_json(self, key: str) -> dict:
        row = await self._session.get(OpsFlagORM, key)
        if not row:
            return {}
        try:
            return json.loads(row.value_json or "{}")
        except json.JSONDecodeError:
            return {}

    async def set_json(self, key: str, value: dict) -> None:
        row = await self._session.get(OpsFlagORM, key)
        raw = json.dumps(value, default=str)
        if row:
            row.value_json = raw
            row.updated_at = utc_now()
        else:
            self._session.add(OpsFlagORM(key=key, value_json=raw, updated_at=utc_now()))
        await self._session.commit()

    async def get_kill_switch(self) -> KillSwitchState:
        data = await self.get_json("kill_switch")
        if not data:
            return KillSwitchState()
        return KillSwitchState.model_validate(data)

    async def set_kill_switch(self, state: KillSwitchState) -> KillSwitchState:
        await self.set_json("kill_switch", state.model_dump(mode="json"))
        return state


class PositionMandateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_domain(self, row: PositionMandateORM) -> PositionMandate:
        return PositionMandate(
            id=row.id,
            symbol=row.symbol,
            qty=row.qty,
            entry_price=row.entry_price,
            opened_at=row.opened_at,
            stop_loss=row.stop_loss,
            take_profit=row.take_profit,
            trailing_pct=row.trailing_pct,
            peak_price=row.peak_price,
            time_stop_days=row.time_stop_days,
            thesis=row.thesis,
            thesis_invalidated=row.thesis_invalidated,
            invalidate_reason=row.invalidate_reason,
            sector=row.sector,
            beta=row.beta,
            status=row.status,
            last_checked_at=row.last_checked_at,
            exit_reason=row.exit_reason,
            closed_at=row.closed_at,
        )

    async def upsert_open(self, mandate: PositionMandate) -> PositionMandate:
        # One open mandate per symbol
        existing = (
            await self._session.execute(
                select(PositionMandateORM).where(
                    PositionMandateORM.symbol == mandate.symbol.upper(),
                    PositionMandateORM.status == "open",
                )
            )
        ).scalar_one_or_none()
        mid = mandate.id or (existing.id if existing else str(uuid4()))
        if existing:
            existing.qty = mandate.qty
            existing.entry_price = mandate.entry_price
            existing.stop_loss = mandate.stop_loss
            existing.take_profit = mandate.take_profit
            existing.trailing_pct = mandate.trailing_pct
            existing.peak_price = mandate.peak_price or existing.peak_price
            existing.time_stop_days = mandate.time_stop_days
            existing.thesis = mandate.thesis or existing.thesis
            existing.sector = mandate.sector or existing.sector
            existing.beta = mandate.beta if mandate.beta is not None else existing.beta
            existing.mandate_json = mandate.model_dump_json()
            await self._session.commit()
            return self._to_domain(existing)

        row = PositionMandateORM(
            id=mid,
            symbol=mandate.symbol.upper(),
            qty=mandate.qty,
            entry_price=mandate.entry_price,
            opened_at=mandate.opened_at or utc_now(),
            stop_loss=mandate.stop_loss,
            take_profit=mandate.take_profit,
            trailing_pct=mandate.trailing_pct,
            peak_price=mandate.peak_price or mandate.entry_price,
            time_stop_days=mandate.time_stop_days,
            thesis=mandate.thesis,
            sector=mandate.sector,
            beta=mandate.beta,
            status="open",
            mandate_json=mandate.model_dump_json(),
        )
        self._session.add(row)
        await self._session.commit()
        mandate.id = mid
        return mandate

    async def list_open(self) -> list[PositionMandate]:
        rows = (
            await self._session.execute(
                select(PositionMandateORM).where(PositionMandateORM.status == "open")
            )
        ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def get_open(self, symbol: str) -> PositionMandate | None:
        row = (
            await self._session.execute(
                select(PositionMandateORM).where(
                    PositionMandateORM.symbol == symbol.upper(),
                    PositionMandateORM.status == "open",
                )
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def save(self, mandate: PositionMandate) -> PositionMandate:
        row = await self._session.get(PositionMandateORM, mandate.id)
        if not row:
            return await self.upsert_open(mandate)
        row.qty = mandate.qty
        row.stop_loss = mandate.stop_loss
        row.take_profit = mandate.take_profit
        row.trailing_pct = mandate.trailing_pct
        row.peak_price = mandate.peak_price
        row.thesis_invalidated = mandate.thesis_invalidated
        row.invalidate_reason = mandate.invalidate_reason
        row.status = mandate.status
        row.last_checked_at = mandate.last_checked_at
        row.exit_reason = mandate.exit_reason
        row.closed_at = mandate.closed_at
        row.mandate_json = mandate.model_dump_json()
        await self._session.commit()
        return mandate
