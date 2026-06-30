"""Tests for alert deduplication."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType
from services.alert_service import AlertService


@pytest.mark.asyncio
async def test_alert_suppressed_on_duplicate():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock()  # existing alert
    session.execute = AsyncMock(return_value=mock_result)

    repo = MagicMock()
    repo._session = session
    repo.save = AsyncMock()

    service = AlertService(repo, cooldown_hours=24)
    alert = Alert(
        ticker="AAPL",
        alert_type=AlertType.BREAKOUT,
        severity=AlertSeverity.HIGH,
        title="test",
        description="test",
    )
    result = await service.emit(alert)
    assert result is None
    repo.save.assert_not_called()
