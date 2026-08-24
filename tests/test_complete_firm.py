"""Tests for complete capital-firm closeout (memory input, autopilot, promotion)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.enums import InvestmentRecommendation
from domain.reports import InvestmentThesis
from services.analysis_service import AnalysisService
from services.auto_execute_service import AutoExecuteService


@pytest.mark.asyncio
async def test_prior_memory_report_sell_bias():
    memory_repo = MagicMock()
    record = MagicMock()
    record.recommendation = "sell"
    record.confidence = 0.7
    record.thesis = "Tesis vieja bajista"
    record.expected_outcome = "caída"
    record.was_correct = False
    record.id = "mem1"
    record.error_tag = "false_short"
    record.evaluated_at = None
    record.created_at = None
    memory_repo.latest_by_ticker = AsyncMock(return_value={"AAA": record})
    memory_repo._session = None

    svc = AnalysisService.__new__(AnalysisService)
    svc._memory_repo = memory_repo
    report = await AnalysisService._prior_memory_report(svc, "AAA")
    assert report is not None
    assert report.agent_name == "investment_memory"
    # Wrong SELL must not stay bearish — otherwise the desk repeats the short.
    assert report.score >= 0


@pytest.mark.asyncio
async def test_prior_memory_report_buy_miss_is_bearish():
    from datetime import datetime, timezone

    memory_repo = MagicMock()
    record = MagicMock()
    record.recommendation = "buy"
    record.confidence = 0.7
    record.thesis = "Tesis alcista fallida"
    record.expected_outcome = "subida"
    record.was_correct = False
    record.error_tag = "false_long"
    record.id = "mem2"
    record.evaluated_at = datetime.now(timezone.utc)
    record.created_at = datetime.now(timezone.utc)
    memory_repo.latest_by_ticker = AsyncMock(return_value={"BBAI": record})
    memory_repo._session = None

    svc = AnalysisService.__new__(AnalysisService)
    svc._memory_repo = memory_repo
    report = await AnalysisService._prior_memory_report(svc, "BBAI")
    assert report is not None
    assert report.score < 0


@pytest.mark.asyncio
async def test_maybe_invalidate_skips_when_not_held():
    svc = AnalysisService.__new__(AnalysisService)
    svc._memory_repo = MagicMock()
    thesis = MagicMock()
    thesis.recommendation = InvestmentRecommendation.SELL
    thesis.executive_summary = "vender"
    thesis.investment_thesis = "vender"

    with patch("services.alpaca_order_service.AlpacaOrderService") as Broker:
        broker = MagicMock()
        broker.is_configured.return_value = True
        broker.get_positions = AsyncMock(return_value=[])
        Broker.return_value = broker
        await AnalysisService._maybe_invalidate_on_sell(svc, "ZZZ", thesis, None)


@pytest.mark.asyncio
async def test_auto_execute_async_blocks_kill_switch():
    session = MagicMock()
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.paper = True

    with patch("services.auto_execute_service.get_settings") as gs, \
         patch("services.auto_execute_service.KillSwitchService") as KS:
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = True
        s.auto_execute_paper_first = False
        s.auto_execute_live = True
        s.auto_execute_max_notional = 25
        s.auto_execute_require_market_open = True
        gs.return_value = s
        KS.return_value.is_active = AsyncMock(return_value=True)
        svc = AutoExecuteService(session, broker)
        ok, reason = await svc.can_auto_trade_async()
        assert ok is False
        assert reason == "kill_switch_active"
