"""Tests for durable open/close briefing catch-up."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services.status_briefing_catchup_service import StatusBriefingCatchupService

ET = ZoneInfo("America/New_York")


@pytest.mark.asyncio
async def test_catchup_sends_open_when_due_and_not_sent():
    session = MagicMock()
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={})
    flags.set_json = AsyncMock()

    with patch("services.status_briefing_catchup_service.OpsFlagRepository", return_value=flags), \
         patch("services.status_briefing_catchup_service.AuditService") as Audit, \
         patch("services.status_briefing_catchup_service.DailyStatusBriefingService") as Brief, \
         patch("services.status_briefing_catchup_service.is_trading_day", return_value=True), \
         patch("services.status_briefing_catchup_service.datetime") as dt:
        Audit.return_value.record = AsyncMock()
        Brief.return_value.send = AsyncMock(
            return_value={"telegram": True, "whatsapp": True, "title": "APERTURA"}
        )
        dt.now.return_value = datetime(2026, 7, 27, 10, 0, tzinfo=ET)
        dt.side_effect = lambda *a, **k: datetime(*a, **k)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("open", via="test")

    assert result["whatsapp"] is True
    flags.set_json.assert_awaited()
    Brief.return_value.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_catchup_skips_already_sent():
    session = MagicMock()
    flags = MagicMock()
    flags.get_json = AsyncMock(
        return_value={"2026-07-27": {"open": {"sent_at": "x", "via": "cron"}}}
    )
    flags.set_json = AsyncMock()

    with patch("services.status_briefing_catchup_service.OpsFlagRepository", return_value=flags), \
         patch("services.status_briefing_catchup_service.AuditService"), \
         patch("services.status_briefing_catchup_service.DailyStatusBriefingService") as Brief, \
         patch("services.status_briefing_catchup_service.is_trading_day", return_value=True), \
         patch("services.status_briefing_catchup_service.datetime") as dt:
        Brief.return_value.send = AsyncMock()
        dt.now.return_value = datetime(2026, 7, 27, 10, 0, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("open", via="test")

    assert result["skipped"] is True
    assert result["reason"] == "already_sent"
    Brief.return_value.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_catchup_skips_outside_window():
    session = MagicMock()
    flags = MagicMock()
    flags.get_json = AsyncMock(return_value={})

    with patch("services.status_briefing_catchup_service.OpsFlagRepository", return_value=flags), \
         patch("services.status_briefing_catchup_service.AuditService"), \
         patch("services.status_briefing_catchup_service.DailyStatusBriefingService") as Brief, \
         patch("services.status_briefing_catchup_service.is_trading_day", return_value=True), \
         patch("services.status_briefing_catchup_service.datetime") as dt:
        Brief.return_value.send = AsyncMock()
        # Before open window
        dt.now.return_value = datetime(2026, 7, 27, 8, 0, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("open", via="test")

    assert result["reason"] == "outside_window"
    Brief.return_value.send.assert_not_awaited()
