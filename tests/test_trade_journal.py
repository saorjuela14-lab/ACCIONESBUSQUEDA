"""Trade journal open/close + track record."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from database.repositories.trade_journal_repository import TradeJournalRepository
from domain.trade_journal import TradeJournalEntry
from services.track_record_service import TrackRecordService
from services.trade_journal_service import TradeJournalService


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
async def test_journal_open_close_pnl(session: AsyncSession):
    svc = TradeJournalService(session)
    opened = await svc.record_open(
        symbol="AAPL",
        qty=10,
        entry_price=100.0,
        stop_loss=92.0,
        take_profit=116.0,
        thesis="test long",
        source_tag="committee",
    )
    assert opened.status == "open"
    assert opened.symbol == "AAPL"

    closed = await svc.record_close(
        symbol="AAPL",
        exit_price=108.0,
        exit_reason="Take-profit near",
    )
    assert closed is not None
    assert closed.status == "closed"
    assert closed.pnl_pct == pytest.approx(8.0)
    assert closed.pnl_usd == pytest.approx(80.0)
    assert closed.r_multiple == pytest.approx(1.0)  # 8% gain / 8% risk


@pytest.mark.asyncio
async def test_track_record_win_rate(session: AsyncSession):
    repo = TradeJournalRepository(session)
    await repo.open_entry(
        TradeJournalEntry(symbol="AAA", qty=1, entry_price=10, stop_loss=9)
    )
    await repo.close_symbol("AAA", exit_price=11, exit_reason="tp")
    await repo.open_entry(
        TradeJournalEntry(symbol="BBB", qty=1, entry_price=10, stop_loss=9)
    )
    await repo.close_symbol("BBB", exit_price=9, exit_reason="stop")

    summary = await TrackRecordService(session).summary(window_days=90)
    assert summary.trades_closed == 2
    assert summary.trades_wins == 1
    assert summary.trades_losses == 1
    assert summary.trades_win_rate_pct == pytest.approx(50.0)
