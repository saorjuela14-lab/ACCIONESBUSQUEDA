"""Tests for open/close WhatsApp portfolio status briefing."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.broker import BrokerAccount, BrokerOrderResult, BrokerPosition
from services.daily_status_briefing_service import DailyStatusBriefingService


@pytest.mark.asyncio
async def test_build_briefing_includes_positions_and_orders():
    broker = MagicMock()
    broker.is_configured.return_value = True
    broker.get_account = AsyncMock(
        return_value=BrokerAccount(
            equity=21.76, cash=3.63, buying_power=3.63, paper=False, status="ACTIVE"
        )
    )
    broker.get_positions = AsyncMock(
        return_value=[
            BrokerPosition(
                symbol="PLUG",
                qty=4,
                market_value=8.38,
                current_price=2.095,
                unrealized_pl=0.5,
                unrealized_plpc=0.06,
            )
        ]
    )
    broker.list_orders = AsyncMock(
        side_effect=lambda status="open", limit=50: (
            [
                BrokerOrderResult(
                    symbol="SOUN", qty=2, side="buy", type="market", status="new"
                )
            ]
            if status == "open"
            else [
                BrokerOrderResult(
                    symbol="PLUG",
                    qty=4,
                    filled_qty=4,
                    side="buy",
                    type="market",
                    status="filled",
                    filled_avg_price=2.0,
                    submitted_at=datetime.now(timezone.utc),
                )
            ]
        )
    )

    with patch("services.daily_status_briefing_service.get_settings") as gs, \
         patch("services.daily_status_briefing_service.RiskPolicyService") as RS:
        s = MagicMock()
        s.firm_autonomy = True
        s.auto_execute_trades = True
        gs.return_value = s
        RS.return_value.status = AsyncMock(
            return_value=MagicMock(
                macro=MagicMock(mode="neutral", trading_allowed=True, block_reason=None)
            )
        )
        title, body = await DailyStatusBriefingService(broker=broker).build("open")

    assert "APERTURA" in title
    assert "PLUG" in body
    assert "ÓRDENES ABIERTAS" in body
    assert "SOUN" in body
    assert "ÓRDENES CERRADAS HOY" in body


@pytest.mark.asyncio
async def test_send_briefing_pushes_channels():
    broker = MagicMock()
    broker.is_configured.return_value = False
    push = MagicMock()
    push.notify_message = AsyncMock(
        return_value={"telegram": True, "whatsapp": True, "webhook": False}
    )

    with patch("services.daily_status_briefing_service.get_settings") as gs:
        gs.return_value = MagicMock(firm_autonomy=True, auto_execute_trades=True)
        result = await DailyStatusBriefingService(broker=broker, push=push).send("close")

    assert result["whatsapp"] is True
    push.notify_message.assert_awaited_once()
    kwargs = push.notify_message.await_args.kwargs
    assert kwargs.get("prefer_plain") is True
