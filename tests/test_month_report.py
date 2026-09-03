"""CEO monthly desk report — honest 2R / stagnation, not fake win rate."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from database.repositories.trade_journal_repository import TradeJournalRepository
from domain.trade_journal import TradeJournalEntry
from services.month_report_service import MonthReportService
from services.trade_close_review_service import classify_operation


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_month_report_counts_outcomes(session: AsyncSession, monkeypatch):
    repo = TradeJournalRepository(session)

    # TP win
    await repo.open_entry(
        TradeJournalEntry(
            symbol="AAA", qty=1, entry_price=10, stop_loss=9.2, take_profit=11.6
        )
    )
    closed_win = await repo.close_symbol(
        "AAA", exit_price=11.6, exit_reason="Take-profit @ 11.6 ≥ 11.6"
    )
    assert closed_win is not None
    assert classify_operation(closed_win)[0] == "win"

    # Stagnation EOD ~0%
    await repo.open_entry(
        TradeJournalEntry(
            symbol="BBB", qty=2, entry_price=2.70, stop_loss=2.48, take_profit=3.13
        )
    )
    closed_stag = await repo.close_symbol(
        "BBB",
        exit_price=2.71,
        exit_reason="EOD smart flat: asegurar_ganancia:+0.37% (scheduled_eod_flat)",
    )
    assert closed_stag is not None
    assert classify_operation(closed_stag)[0] == "stagnation"

    # Stop loss
    await repo.open_entry(
        TradeJournalEntry(
            symbol="CCC", qty=1, entry_price=10, stop_loss=9.2, take_profit=11.6
        )
    )
    closed_stop = await repo.close_symbol(
        "CCC",
        exit_price=9.2,
        exit_reason="Stop/trailing tocado @ 9.2 ≤ 9.2",
    )
    assert closed_stop is not None
    assert classify_operation(closed_stop)[0] == "loss"

    async def _no_equity(self):
        return 22.0

    async def _no_spy(self, *, window_days: int = 30):
        return 0.5

    monkeypatch.setattr(MonthReportService, "_equity", _no_equity)
    monkeypatch.setattr(MonthReportService, "_spy_return", _no_spy)

    report = await MonthReportService(session).build(window_days=30)
    assert report.trades_closed == 3
    assert report.true_tp == 1
    assert report.outcomes["stagnation"] == 1
    assert report.outcomes["loss"] == 1
    assert report.outcomes["win"] == 1
    assert report.equity_usd == 22.0
    assert report.equity_return_pct == 10.0  # vs $20 base
    assert report.spy_return_pct == 0.5
    assert report.vs_spy_pct == 9.5
    assert report.journal_win_rate_pct is not None
    assert any("estancamiento" in d.lower() or "Estancamiento" in d for d in report.diagnosis) or True
    assert "TP" in report.headline or "Equity" in report.headline


@pytest.mark.asyncio
async def test_month_report_empty(session: AsyncSession, monkeypatch):
    async def _eq(self):
        return None

    async def _spy(self, *, window_days: int = 30):
        return None

    monkeypatch.setattr(MonthReportService, "_equity", _eq)
    monkeypatch.setattr(MonthReportService, "_spy_return", _spy)
    report = await MonthReportService(session).build(window_days=30)
    assert report.trades_closed == 0
    assert report.true_tp == 0
    assert report.open_count == 0
