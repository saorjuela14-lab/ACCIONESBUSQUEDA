"""Daily memory evaluation + 24h lessons so the desk does not repeat errors."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, InvestmentMemoryORM
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from services.desk_learning_service import DeskLearningService, classify_error
from services.memory_evaluation_service import MemoryEvaluationService


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _row(
    *,
    ticker: str,
    rec: str = "buy",
    price: float = 100.0,
    created_at: datetime | None = None,
    scores: dict | None = None,
) -> InvestmentMemoryORM:
    import json

    now = created_at or datetime.now(timezone.utc)
    return InvestmentMemoryORM(
        id=str(uuid4()),
        ticker=ticker,
        thesis="t",
        reasons_json="[]",
        scores_json=json.dumps(scores or {"technical_agent": 20}),
        confidence=0.6,
        scenario="base",
        expected_outcome="up",
        recommendation=rec,
        entry_price=price,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_ready_for_evaluation_uses_hours(session: AsyncSession):
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _row(ticker="OLD", created_at=now - timedelta(hours=6)),
            _row(ticker="NEW", created_at=now - timedelta(hours=1)),
        ]
    )
    await session.commit()
    repo = InvestmentMemoryRepository(session)
    ready = await repo.list_ready_for_evaluation(min_hours=5)
    tickers = {r.ticker for r in ready}
    assert "OLD" in tickers
    assert "NEW" not in tickers


def test_daily_hit_threshold():
    svc = MemoryEvaluationService.__new__(MemoryEvaluationService)
    svc._hit_pct = 1.5
    assert svc._was_correct("buy", 2.0) is True
    assert svc._was_correct("buy", 0.4) is False
    assert svc._was_correct("sell", -2.0) is True
    assert svc._was_correct("hold", 0.3) is True
    assert svc._was_correct("hold", 3.0) is False


def test_classify_error_tags():
    assert classify_error("buy", False) == "false_long"
    assert classify_error("sell", False) == "false_short"
    assert classify_error("hold", False) == "hold_miss"
    assert classify_error("buy", True) is None


@pytest.mark.asyncio
async def test_false_long_becomes_avoid_ticker(session: AsyncSession, monkeypatch):
    monkeypatch.setattr(
        "services.memory_evaluation_service.get_settings",
        lambda: MagicMock(
            memory_evaluation_days=1,
            memory_evaluation_min_hours=4.0,
            memory_hit_pct=1.5,
            memory_avoid_hours=24,
            agent_weights_auto_calibrate=True,
        ),
    )
    monkeypatch.setattr(
        "services.desk_learning_service.get_settings",
        lambda: MagicMock(memory_avoid_hours=24),
    )
    now = datetime.now(timezone.utc)
    session.add(_row(ticker="BBAI", rec="buy", created_at=now - timedelta(hours=6)))
    await session.commit()

    market = AsyncMock()
    market.get_quote = AsyncMock(return_value={"current_price": 97.0})
    repo = InvestmentMemoryRepository(session)
    result = await MemoryEvaluationService(repo, market).evaluate_pending()

    assert result["evaluated"] == 1
    assert result["incorrect"] == 1
    assert "BBAI" in (result.get("avoid_tickers") or [])

    learning = DeskLearningService(session)
    assert "BBAI" in await learning.avoid_tickers()
    merged = await learning.merge_excludes(["AAPL"])
    assert set(merged) == {"AAPL", "BBAI"}
