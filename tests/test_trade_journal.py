"""Trade journal open/close + track record."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from database.repositories.trade_journal_repository import TradeJournalRepository
from domain.trade_journal import TradeJournalEntry
from services.track_record_service import TrackRecordService
from services.trade_journal_service import TradeJournalService
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
    assert review["outcome"] == "win"
    assert review["was_correct"] is True
    by_name = {m["agent"]: m for m in review["members"]}
    assert by_name["technical_agent"]["right"] is True
    assert by_name["news_agent"]["right"] is False
    assert "macro_agent" not in by_name  # |score| < 5 skipped
    assert by_name["technical_agent"].get("why")
    assert by_name["news_agent"].get("pattern") == "false_veto"

    # Stop = operación perdida (not P&L)
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
    await svc.record_close(
        symbol="PLUG",
        exit_price=2.02,
        exit_reason="Stop/trailing tocado @ 2.0200 ≤ 2.0200",
    )
    plug = next(r for r in await svc.list_closed(days=1) if r.symbol == "PLUG")
    plug_review = plug.meta["member_review"]
    assert plug_review["outcome"] == "loss"
    assert plug_review["was_correct"] is False
    plug_by = {m["agent"]: m for m in plug_review["members"]}
    assert plug_by["technical_agent"]["right"] is False
    assert plug_by["news_agent"]["right"] is True

    # EOD / gestión: no veredicto por P&L
    await svc.record_open(
        symbol="BBAI", qty=2, entry_price=3.20, stop_loss=2.94, take_profit=3.71
    )
    await InvestmentMemoryRepository(session).save(
        InvestmentMemoryRecord(
            ticker="BBAI",
            thesis="buy bbai",
            scores={"technical_agent": 22, "news_agent": -10},
            confidence=0.5,
            scenario="base",
            expected_outcome="up",
            recommendation="buy",
            entry_price=3.20,
        )
    )
    await svc.record_close(
        symbol="BBAI",
        exit_price=3.17,
        exit_reason="EOD smart flat: carry_rojo (scheduled_eod_flat)",
    )
    bbai = next(r for r in await svc.list_closed(days=1) if r.symbol == "BBAI")
    bbai_review = bbai.meta["member_review"]
    assert bbai_review["outcome"] == "gestion"
    assert bbai_review["was_correct"] is None
    assert bbai_review["right"] == []
    assert bbai_review["wrong"] == []


@pytest.mark.asyncio
async def test_tiny_eod_is_stagnation_and_avoids_ticker(session: AsyncSession):
    from domain.entities import InvestmentMemoryRecord
    from database.repositories.investment_memory_repository import InvestmentMemoryRepository
    from services.desk_learning_service import DeskLearningService

    await InvestmentMemoryRepository(session).save(
        InvestmentMemoryRecord(
            ticker="AMC",
            thesis="buy amc",
            scores={"technical_agent": 20, "news_agent": -12},
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
    await svc.record_close(
        symbol="AMC",
        exit_price=2.705,
        exit_reason="EOD smart flat: asegurar_ganancia:+0.19% (eod_flat_window_20m)",
    )
    closed = (await svc.list_closed(days=1))[0]
    review = closed.meta["member_review"]
    assert review["outcome"] == "stagnation"
    assert review["was_correct"] is False
    by_name = {m["agent"]: m for m in review["members"]}
    assert by_name["technical_agent"]["right"] is False
    assert by_name["technical_agent"]["pattern"] == "stagnation_failed"
    assert by_name["news_agent"]["right"] is True
    learning = DeskLearningService(session)
    assert "AMC" in await learning.avoid_tickers()


@pytest.mark.asyncio
async def test_stagnation_avoids_ticker_without_memory_scores(session: AsyncSession):
    from services.trade_close_review_service import TradeCloseReviewService
    from services.desk_learning_service import DeskLearningService

    svc = TradeJournalService(session)
    await svc.record_open(symbol="AMC", qty=2, entry_price=2.70, stop_loss=2.48, take_profit=3.13)
    await svc.record_close(
        symbol="AMC",
        exit_price=2.705,
        exit_reason="posición ausente en Alpaca",
    )
    closed = (await svc.list_closed(days=1))[0]
    assert closed.meta["member_review"]["outcome"] == "stagnation"
    assert "AMC" in await DeskLearningService(session).avoid_tickers()
    avoided = await TradeCloseReviewService(session).refresh_stagnation_avoids()
    assert "AMC" in avoided


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


def test_classify_operation_is_not_pnl():
    win = TradeJournalEntry(
        symbol="AMC", qty=2, entry_price=2.7, stop_loss=2.48, take_profit=3.13,
        exit_price=2.6, exit_reason="Take-profit @ 3.14 ≥ 3.13", status="closed",
    )
    loss = TradeJournalEntry(
        symbol="AMC", qty=2, entry_price=2.7, stop_loss=2.48, take_profit=3.13,
        exit_price=2.48, exit_reason="Stop/trailing tocado @ 2.48 ≤ 2.48", status="closed",
    )
    red_eod = TradeJournalEntry(
        symbol="AMC", qty=2, entry_price=2.7, stop_loss=2.48, take_profit=3.13,
        exit_price=2.67, exit_reason="EOD smart flat: asegurar_ganancia:-1.0%", status="closed",
    )
    tiny_green = TradeJournalEntry(
        symbol="AMC", qty=2, entry_price=2.6974, stop_loss=2.48, take_profit=3.13,
        exit_price=2.70, pnl_pct=0.10,
        exit_reason="EOD smart flat: asegurar_ganancia:+0.10% (eod_flat_window_20m)",
        status="closed",
    )
    banked = TradeJournalEntry(
        symbol="PLUG", qty=1, entry_price=2.03, stop_loss=1.87, take_profit=2.35,
        exit_price=2.25, pnl_pct=10.84,
        exit_reason="EOD smart flat: asegurar_ganancia:+10.84% (scheduled_eod_flat)",
        status="closed",
    )
    carry = TradeJournalEntry(
        symbol="AMC", qty=2, entry_price=2.7, stop_loss=2.48, take_profit=3.13,
        exit_price=2.67, exit_reason="EOD smart flat: carry_rojo (scheduled_eod_flat)",
        status="closed",
    )
    assert classify_operation(win)[0] == "win"
    assert classify_operation(loss)[0] == "loss"
    assert classify_operation(red_eod)[0] == "stagnation"
    assert classify_operation(tiny_green) == ("stagnation", "no_progress")
    assert classify_operation(banked)[0] == "gestion"
    assert classify_operation(carry)[0] == "gestion"


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
