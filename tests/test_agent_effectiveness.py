"""Per-agent decision effectiveness from evaluated memory."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base, InvestmentMemoryORM
from services.agent_effectiveness_service import AgentEffectivenessService


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _mem(
    *,
    ticker: str,
    scores: dict,
    was_correct: bool,
    ret: float,
    rec: str = "buy",
) -> InvestmentMemoryORM:
    now = datetime.now(timezone.utc)
    return InvestmentMemoryORM(
        id=str(uuid4()),
        ticker=ticker,
        thesis="t",
        reasons_json="[]",
        scores_json=json.dumps(scores),
        confidence=0.6,
        scenario="base",
        expected_outcome="up",
        recommendation=rec,
        entry_price=100.0,
        created_at=now,
        evaluated_at=now,
        was_correct=was_correct,
        actual_return_pct=ret,
    )


@pytest.mark.asyncio
async def test_agent_directional_hit_rate(session: AsyncSession):
    # Technical bullish + up → hit; technical bullish + down → miss
    # News bearish + down → hit
    session.add_all(
        [
            _mem(
                ticker="AAA",
                scores={"technical_agent": 20, "news_agent": -2},
                was_correct=True,
                ret=8.0,
            ),
            _mem(
                ticker="BBB",
                scores={"technical_agent": 18, "news_agent": -15},
                was_correct=False,
                ret=-6.0,
            ),
            _mem(
                ticker="CCC",
                scores={"technical_agent": 22, "news_agent": -12},
                was_correct=True,
                ret=4.0,
            ),
        ]
    )
    await session.commit()

    summary = await AgentEffectivenessService(session, score_threshold=5.0).summary(
        window_days=90
    )
    assert summary.theses_evaluated == 3
    assert summary.theses_correct == 2
    assert summary.desk_hit_rate_pct == pytest.approx(66.7, abs=0.1)

    by_name = {a.agent_name: a for a in summary.agents}
    tech = by_name["technical_agent"]
    # AAA hit, BBB miss, CCC hit → 2/3
    assert tech.samples == 3
    assert tech.hits == 2
    assert tech.hit_rate_pct == pytest.approx(66.7, abs=0.1)

    news = by_name["news_agent"]
    # only BBB and CCC have |score|>=5: BBB bearish+down hit, CCC bearish+up miss → 50%
    assert news.samples == 2
    assert news.hit_rate_pct == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_empty_effectiveness(session: AsyncSession):
    summary = await AgentEffectivenessService(session).summary(window_days=90)
    assert summary.theses_evaluated == 0
    assert summary.desk_hit_rate_pct is None
    assert summary.agents  # defaults listed with 0 samples
