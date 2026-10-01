"""Open/close durable trade journal entries from lifecycle fills."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.trade_journal_repository import TradeJournalRepository
from domain.trade_journal import TradeJournalEntry
from utils.logging import get_logger

logger = get_logger(__name__)


class TradeJournalService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = TradeJournalRepository(session)

    async def record_open(
        self,
        *,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        thesis: str | None = None,
        source_tag: str | None = None,
        mandate_id: str | None = None,
        meta: dict | None = None,
    ) -> TradeJournalEntry:
        entry = TradeJournalEntry(
            symbol=symbol.upper(),
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            thesis=thesis,
            source_tag=source_tag,
            mandate_id=mandate_id,
            meta=meta or {},
        )
        saved = await self._repo.open_entry(entry)
        logger.info(
            "trade_journal.open",
            symbol=saved.symbol,
            entry=saved.entry_price,
            qty=saved.qty,
            id=saved.id,
        )
        return saved

    async def record_close(
        self,
        *,
        symbol: str,
        exit_price: float,
        exit_reason: str | None = None,
        closed_at: datetime | None = None,
        fill_entry_price: float | None = None,
    ) -> TradeJournalEntry | None:
        closed = await self._repo.close_symbol(
            symbol,
            exit_price=exit_price,
            exit_reason=exit_reason,
            closed_at=closed_at,
            fill_entry_price=fill_entry_price,
        )
        if closed:
            logger.info(
                "trade_journal.close",
                symbol=closed.symbol,
                exit=closed.exit_price,
                pnl_pct=closed.pnl_pct,
                reason=(exit_reason or "")[:120],
            )
            try:
                from services.trade_close_review_service import TradeCloseReviewService

                await TradeCloseReviewService(self._repo._session).review_closed(closed)
            except Exception as exc:
                logger.warning(
                    "trade_close.member_review_failed",
                    symbol=closed.symbol,
                    error=str(exc),
                )
        return closed

    async def record_stop_adjust(
        self,
        *,
        symbol: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reason: str = "trailing",
    ) -> TradeJournalEntry | None:
        """Record a trailing / stop-TP ratchet on an open journal row. Does not trade."""
        open_e = await self._repo.get_open(symbol)
        if not open_e:
            return None
        row_id = open_e.id
        from database.models import TradeJournalORM
        import json

        row = await self._repo._session.get(TradeJournalORM, row_id)
        if not row:
            return None
        old_stop = row.stop_loss
        old_tp = row.take_profit
        if stop_loss is not None:
            row.stop_loss = float(stop_loss)
        if take_profit is not None:
            row.take_profit = float(take_profit)
        try:
            meta = json.loads(row.meta_json or "{}")
        except json.JSONDecodeError:
            meta = {}
        hist = list(meta.get("stop_adjustments") or [])
        hist.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "old_stop": old_stop,
                "new_stop": row.stop_loss,
                "old_tp": old_tp,
                "new_tp": row.take_profit,
            }
        )
        meta["stop_adjustments"] = hist[-20:]
        row.meta_json = json.dumps(meta, default=str)
        await self._repo._session.commit()
        logger.info(
            "trade_journal.stop_adjust",
            symbol=symbol,
            old_stop=old_stop,
            new_stop=row.stop_loss,
            reason=reason,
        )
        return self._repo._to_domain(row)

    async def list_recent(self, limit: int = 40) -> list[TradeJournalEntry]:
        return await self._repo.list_recent(limit=limit)

    async def list_closed(self, *, limit: int = 40, days: int | None = 90) -> list[TradeJournalEntry]:
        return await self._repo.list_closed(limit=limit, days=days)

    async def list_open(self) -> list[TradeJournalEntry]:
        return await self._repo.list_open()
