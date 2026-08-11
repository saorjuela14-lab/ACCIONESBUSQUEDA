"""Product track record: journal win rate + investment memory hit rate."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import InvestmentMemoryORM
from database.repositories.trade_journal_repository import TradeJournalRepository
from database.url import is_sqlite, normalize_database_url
from domain.trade_journal import TrackRecordSummary


class TrackRecordService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._journal = TradeJournalRepository(session)

    async def summary(self, *, window_days: int = 90, recent_limit: int = 12) -> TrackRecordSummary:
        closed = await self._journal.list_closed(limit=500, days=window_days)
        open_rows = await self._journal.list_open()

        wins = [t for t in closed if (t.pnl_pct or 0) > 0]
        losses = [t for t in closed if (t.pnl_pct or 0) <= 0 and t.pnl_pct is not None]
        scored = [t for t in closed if t.pnl_pct is not None]
        win_rate = (len(wins) / len(scored) * 100.0) if scored else None
        avg_pnl = (sum(t.pnl_pct or 0 for t in scored) / len(scored)) if scored else None
        total_pnl = sum(t.pnl_usd or 0 for t in closed if t.pnl_usd is not None) or None

        mem_rows = (
            await self._session.execute(
                select(InvestmentMemoryORM).where(InvestmentMemoryORM.was_correct.is_not(None))
            )
        ).scalars().all()
        # Filter memory by evaluated_at within window when present
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        mem_eval = [
            r
            for r in mem_rows
            if r.evaluated_at is None or r.evaluated_at >= since
        ]
        mem_correct = sum(1 for r in mem_eval if r.was_correct is True)
        mem_hit = (mem_correct / len(mem_eval) * 100.0) if mem_eval else None

        url = normalize_database_url(get_settings().database_url)
        durable = not is_sqlite(url)

        return TrackRecordSummary(
            window_days=window_days,
            trades_closed=len(closed),
            trades_wins=len(wins),
            trades_losses=len(losses),
            trades_win_rate_pct=round(win_rate, 1) if win_rate is not None else None,
            trades_avg_pnl_pct=round(avg_pnl, 2) if avg_pnl is not None else None,
            trades_expectancy_pct=round(avg_pnl, 2) if avg_pnl is not None else None,
            trades_total_pnl_usd=round(total_pnl, 2) if total_pnl is not None else None,
            memory_evaluated=len(mem_eval),
            memory_correct=mem_correct,
            memory_hit_rate_pct=round(mem_hit, 1) if mem_hit is not None else None,
            open_positions=len(open_rows),
            recent_closed=closed[:recent_limit],
            durable_db=durable,
        )
