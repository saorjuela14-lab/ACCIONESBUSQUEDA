"""USD parking (USDT/USDC) detection and autopilot sweep."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.broker import BrokerPosition
from services.autopilot_service import AutopilotService, is_usd_parking_symbol


def test_is_usd_parking_symbol():
    assert is_usd_parking_symbol("USDTUSD")
    assert is_usd_parking_symbol("USDT/USD")
    assert is_usd_parking_symbol("usdcusd")
    assert is_usd_parking_symbol("USDC/USD")
    assert not is_usd_parking_symbol("AAPL")
    assert not is_usd_parking_symbol("BTCUSD")
    assert not is_usd_parking_symbol("")


@pytest.mark.asyncio
async def test_sweep_closes_usdt_only():
    session = MagicMock()
    svc = AutopilotService(session)
    svc._broker = MagicMock()
    svc._broker.is_configured.return_value = True
    svc._broker.get_positions = AsyncMock(
        return_value=[
            BrokerPosition(symbol="USDT/USD", qty=21.7, market_value=21.7, asset_class="crypto"),
            BrokerPosition(symbol="AAPL", qty=1, market_value=100, asset_class="us_equity"),
        ]
    )
    svc._broker.close_position = AsyncMock(
        return_value={"id": "ord-1", "status": "pending_new", "symbol": "USDT/USD"}
    )

    out = await svc._sweep_usd_parking()
    assert len(out["closed"]) == 1
    assert out["closed"][0]["symbol"] == "USDTUSD"
    svc._broker.close_position.assert_awaited_once_with("USDTUSD")


@pytest.mark.asyncio
async def test_sweep_noop_when_no_parking():
    session = MagicMock()
    svc = AutopilotService(session)
    svc._broker = MagicMock()
    svc._broker.is_configured.return_value = True
    svc._broker.get_positions = AsyncMock(
        return_value=[BrokerPosition(symbol="AAPL", qty=1, market_value=100)]
    )
    svc._broker.close_position = AsyncMock()
    out = await svc._sweep_usd_parking()
    assert out["closed"] == []
    svc._broker.close_position.assert_not_awaited()
