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
async def test_close_grades_committee_members(session: AsyncSession):
    from domain.entities import InvestmentMemoryRecord
    from database.repositories.investment_memory_repository import InvestmentMemoryRepository

    await InvestmentMemoryRepository(session).save(
        InvestmentMemoryRecord(
            ticker="AMC",
            thesis="buy amc",
            scores={"technical_agent": 20, "news_agent": -15, "macro_agent": 2},
            confidence=0.6,
            scenario="base",
            expected_outcome="up",
            recommendation="buy",
            entry_price=2.70,
        )
    )
    svc = TradeJournalService(session)
    await svc.record_open(
        symbol="AMC", qty=2, entry_price=2.70, stop_loss=2.48, take_profit=3.13
    )
    await svc.record_close(symbol="AMC", exit_price=2.90, exit_reason="take profit")
    closed = (await svc.list_closed(days=1))[0]
    review = closed.meta["member_review"]
    assert review["was_correct"] is True
    by_name = {m["agent"]: m for m in review["members"]}
    assert by_name["technical_agent"]["right"] is True
    assert by_name["news_agent"]["right"] is False
    assert "macro_agent" not in by_name  # |score| < 5 skipped

    # Losing close grades the other way
    await svc.record_open(
        symbol="PLUG", qty=1, entry_price=2.20, stop_loss=2.02, take_profit=2.55
    )
    await InvestmentMemoryRepository(session).save(
        InvestmentMemoryRecord(
            ticker="PLUG",
            thesis="buy plug",
            scores={"technical_agent": 18, "news_agent": -12},
            confidence=0.5,
            scenario="base",
            expected_outcome="up",
            recommendation="buy",
            entry_price=2.20,
        )
    )
    await svc.record_close(symbol="PLUG", exit_price=2.10, exit_reason="eod")
    plug = next(r for r in await svc.list_closed(days=1) if r.symbol == "PLUG")
    plug_review = plug.meta["member_review"]
    assert plug_review["was_correct"] is False
    plug_by = {m["agent"]: m for m in plug_review["members"]}
    assert plug_by["technical_agent"]["right"] is False
    assert plug_by["news_agent"]["right"] is True


@pytest.mark.asyncio
async def test_close_uses_fill_entry_price(session: AsyncSession):
    svc = TradeJournalService(session)
    await svc.record_open(symbol="AMC", qty=2, entry_price=2.48, stop_loss=2.48)
    closed = await svc.record_close(
        symbol="AMC",
        exit_price=2.67,
        exit_reason="mark",
        fill_entry_price=2.6974,
    )
    assert closed.entry_price == pytest.approx(2.6974)
    assert closed.pnl_pct < 0


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
