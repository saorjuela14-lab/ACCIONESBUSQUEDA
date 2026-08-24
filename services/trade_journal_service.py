"""Open/close durable trade journal entries from lifecycle fills."""

from __future__ import annotations

from datetime import datetime

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

    async def list_recent(self, limit: int = 40) -> list[TradeJournalEntry]:
        return await self._repo.list_recent(limit=limit)

    async def list_closed(self, *, limit: int = 40, days: int | None = 90) -> list[TradeJournalEntry]:
        return await self._repo.list_closed(limit=limit, days=days)

    async def list_open(self) -> list[TradeJournalEntry]:
        return await self._repo.list_open()
