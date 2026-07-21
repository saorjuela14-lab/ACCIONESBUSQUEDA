"""Tests for manage-capital capital resolution (not HTML default $1000)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apis.routes.recommendations import _resolve_manage_capital


@pytest.mark.asyncio
async def test_resolve_prefers_requested_capital():
    session = MagicMock()
    assert await _resolve_manage_capital(session, 22.5) == 22.5


@pytest.mark.asyncio
async def test_resolve_uses_alpaca_equity_when_no_request():
    session = MagicMock()
    account = MagicMock()
    account.equity = 23.4
    account.portfolio_value = 23.4
    account.cash = 10.0
    account.buying_power = 10.0

    mock_svc = MagicMock()
    mock_svc.is_configured.return_value = True
    mock_svc.get_account = AsyncMock(return_value=account)

    with patch("services.alpaca_order_service.AlpacaOrderService", return_value=mock_svc):
        assert await _resolve_manage_capital(session, None) == 23.4


@pytest.mark.asyncio
async def test_resolve_falls_back_to_portfolio_cash():
    session = MagicMock()
    pf = MagicMock()
    pf.cash = 18.0
    pf.initial_capital = 1000.0
    pf.updated_at = datetime.now(timezone.utc)
    pf.total_value = None

    mock_alpaca = MagicMock()
    mock_alpaca.is_configured.return_value = False

    with patch("services.alpaca_order_service.AlpacaOrderService", return_value=mock_alpaca), \
         patch("apis.routes.recommendations.PortfolioRepository") as Repo:
        Repo.return_value.list_all = AsyncMock(return_value=[pf])
        assert await _resolve_manage_capital(session, None) == 18.0


@pytest.mark.asyncio
async def test_resolve_errors_when_nothing_available():
    session = MagicMock()
    mock_alpaca = MagicMock()
    mock_alpaca.is_configured.return_value = False

    with patch("services.alpaca_order_service.AlpacaOrderService", return_value=mock_alpaca), \
         patch("apis.routes.recommendations.PortfolioRepository") as Repo:
        Repo.return_value.list_all = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as ei:
            await _resolve_manage_capital(session, None)
        assert ei.value.status_code == 400
