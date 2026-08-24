"""Trade journal persistence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TradeJournalORM, utc_now
from domain.trade_journal import TradeJournalEntry


class TradeJournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_domain(self, row: TradeJournalORM) -> TradeJournalEntry:
        meta: dict = {}
        try:
            meta = json.loads(row.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        return TradeJournalEntry(
            id=row.id,
            symbol=row.symbol,
            status=row.status,  # type: ignore[arg-type]
            qty=row.qty,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            stop_loss=row.stop_loss,
            take_profit=row.take_profit,
            pnl_usd=row.pnl_usd,
            pnl_pct=row.pnl_pct,
            r_multiple=row.r_multiple,
            thesis=row.thesis,
            source_tag=row.source_tag,
            exit_reason=row.exit_reason,
            mandate_id=row.mandate_id,
            opened_at=row.opened_at,
            closed_at=row.closed_at,
            meta=meta,
        )

    async def open_entry(self, entry: TradeJournalEntry) -> TradeJournalEntry:
        """Open a journal row; refresh if same symbol already open."""
        existing = await self.get_open(entry.symbol)
        eid = entry.id or (existing.id if existing else str(uuid4()))
        if existing:
            existing_row = await self._session.get(TradeJournalORM, existing.id)
            if existing_row:
                existing_row.qty = entry.qty
                existing_row.entry_price = entry.entry_price
                existing_row.stop_loss = entry.stop_loss
                existing_row.take_profit = entry.take_profit
                existing_row.thesis = entry.thesis or existing_row.thesis
                existing_row.source_tag = entry.source_tag or existing_row.source_tag
                existing_row.mandate_id = entry.mandate_id or existing_row.mandate_id
                existing_row.meta_json = json.dumps(entry.meta or {}, default=str)
                await self._session.commit()
                return self._to_domain(existing_row)

        row = TradeJournalORM(
            id=eid,
            symbol=entry.symbol.upper(),
            status="open",
            qty=entry.qty,
            entry_price=entry.entry_price,
            stop_loss=entry.stop_loss,
            take_profit=entry.take_profit,
            thesis=entry.thesis,
            source_tag=entry.source_tag,
            mandate_id=entry.mandate_id,
            opened_at=entry.opened_at or utc_now(),
            meta_json=json.dumps(entry.meta or {}, default=str),
        )
        self._session.add(row)
        await self._session.commit()
        entry.id = eid
        return entry

    async def get_open(self, symbol: str) -> TradeJournalEntry | None:
        row = (
            await self._session.execute(
                select(TradeJournalORM).where(
                    TradeJournalORM.symbol == symbol.upper(),
                    TradeJournalORM.status == "open",
                )
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def close_symbol(
        self,
        symbol: str,
        *,
        exit_price: float,
        exit_reason: str | None = None,
        closed_at: datetime | None = None,
        fill_entry_price: float | None = None,
    ) -> TradeJournalEntry | None:
        open_entry = await self.get_open(symbol)
        if not open_entry:
            return None
        row = await self._session.get(TradeJournalORM, open_entry.id)
        if not row:
            return None
        if fill_entry_price is not None and float(fill_entry_price) > 0:
            row.entry_price = float(fill_entry_price)
        entry_px = float(row.entry_price or 0)
        qty = float(row.qty or 0)
        exit_px = float(exit_price)
        pnl_usd = (exit_px - entry_px) * qty if entry_px and qty else None
        pnl_pct = ((exit_px - entry_px) / entry_px * 100.0) if entry_px > 0 else None
        r_mult = None
        stop = row.stop_loss
        if stop is not None and entry_px > float(stop) and pnl_pct is not None:
            risk_pct = (entry_px - float(stop)) / entry_px * 100.0
            if risk_pct > 0:
                r_mult = round(pnl_pct / risk_pct, 2)

        row.status = "closed"
        row.exit_price = exit_px
        row.exit_reason = exit_reason
        row.closed_at = closed_at or utc_now()
        row.pnl_usd = round(pnl_usd, 4) if pnl_usd is not None else None
        row.pnl_pct = round(pnl_pct, 4) if pnl_pct is not None else None
        row.r_multiple = r_mult
        await self._session.commit()
        return self._to_domain(row)

    async def save_meta(self, entry_id: str, meta: dict) -> None:
        row = await self._session.get(TradeJournalORM, entry_id)
        if not row:
            return
        row.meta_json = json.dumps(meta or {}, default=str)
        await self._session.commit()

    async def list_closed(self, *, limit: int = 40, days: int | None = 90) -> list[TradeJournalEntry]:
        q = select(TradeJournalORM).where(TradeJournalORM.status == "closed")
        if days is not None and days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.where(TradeJournalORM.closed_at >= since)
        q = q.order_by(TradeJournalORM.closed_at.desc()).limit(limit)
        rows = (await self._session.execute(q)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_open(self) -> list[TradeJournalEntry]:
        rows = (
            await self._session.execute(
                select(TradeJournalORM).where(TradeJournalORM.status == "open")
            )
        ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_recent(self, *, limit: int = 40) -> list[TradeJournalEntry]:
        q = select(TradeJournalORM).order_by(TradeJournalORM.opened_at.desc()).limit(limit)
        rows = (await self._session.execute(q)).scalars().all()
        return [self._to_domain(r) for r in rows]
