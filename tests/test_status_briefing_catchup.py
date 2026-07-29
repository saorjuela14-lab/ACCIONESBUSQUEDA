"""Tests for durable open/lunch/close briefing catch-up (no spam)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services.status_briefing_catchup_service import StatusBriefingCatchupService

ET = ZoneInfo("America/New_York")


@pytest.mark.asyncio
async def test_catchup_sends_lunch_when_due():
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
            return_value={"telegram": True, "whatsapp": True, "title": "ALMUERZO"}
        )
        dt.now.return_value = datetime(2026, 7, 27, 12, 35, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("lunch", via="test")

    assert result["whatsapp"] is True
    Brief.return_value.send.assert_awaited_once()
    flags.set_json.assert_awaited()


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
async def test_cold_start_still_sends_missed_open_after_noon():
    """If the host slept through 09:35, a 13:00 wake must still deliver open once."""
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
        dt.now.return_value = datetime(2026, 7, 28, 13, 5, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("open", via="startup_catchup")

    assert result["whatsapp"] is True
    Brief.return_value.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_evening_still_sends_missed_close():
    """Close at 16:05 must still recover at 20:57 if the host slept."""
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
            return_value={"telegram": True, "whatsapp": True, "title": "CIERRE"}
        )
        dt.now.return_value = datetime(2026, 7, 28, 20, 57, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("close", via="health_catchup")

    assert result["whatsapp"] is True
    Brief.return_value.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_delivery_not_marked_sent():
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
            return_value={"telegram": False, "whatsapp": False, "title": "APERTURA"}
        )
        dt.now.return_value = datetime(2026, 7, 28, 10, 0, tzinfo=ET)

        svc = StatusBriefingCatchupService(session)
        result = await svc.send_if_needed("open", via="test")

    assert result["whatsapp"] is False
    flags.set_json.assert_not_awaited()
