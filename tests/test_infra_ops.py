"""Infra: last Autopilot cycle (read-only) and journal trailing records."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from database.repositories.ops_repository import OpsFlagRepository
from services.trade_journal_service import TradeJournalService


def test_fastapi_cloud_workflow_does_not_use_secrets_in_job_if():
    text = Path(".github/workflows/fastapi-cloud-deploy.yml").read_text()
    assert "if: ${{ secrets." not in text
    assert "fastapi-cloud[bot]" in text or "fastapi-cloud" in text


def test_keepalive_cron_not_sub_five_minutes():
    text = Path(".github/workflows/desk-keepalive.yml").read_text()
    assert "*/5 " not in text
    assert "*/15" in text
    assert "UptimeRobot" in text or "5 min" in text


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
async def test_journal_records_trailing_stop_adjust(session: AsyncSession):
    svc = TradeJournalService(session)
    opened = await svc.record_open(
        symbol="SNAP", qty=1, entry_price=10.0, stop_loss=9.2, take_profit=11.6
    )
    assert opened.stop_loss == 9.2
    adj = await svc.record_stop_adjust(symbol="SNAP", stop_loss=9.5, reason="trailing +1R")
    assert adj is not None
    assert adj.stop_loss == 9.5
    hist = (adj.meta or {}).get("stop_adjustments") or []
    assert hist and hist[-1]["reason"] == "trailing +1R"
    assert hist[-1]["old_stop"] == 9.2


@pytest.mark.asyncio
async def test_last_cycle_flag_roundtrip(session: AsyncSession):
    await OpsFlagRepository(session).set_json(
        "firm_autopilot_last_cycle",
        {"at": "2026-10-01T12:00:00Z", "result": "ok", "message": "picks=0"},
    )
    data = await OpsFlagRepository(session).get_json("firm_autopilot_last_cycle")
    assert data["result"] == "ok"
    assert data["message"] == "picks=0"
