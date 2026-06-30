"""Watchlist matrix service tests."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from domain.entities import InvestmentMemoryRecord, WatchlistItem
from services.watchlist_matrix_service import WatchlistMatrixService


@pytest.fixture
def market():
    m = MagicMock()
    m.get_quote = AsyncMock(return_value={
        "ticker": "VRT",
        "company_name": "Vertiv",
        "current_price": 100.0,
    })
    m.get_history = AsyncMock(return_value=pd.DataFrame({
        "Close": [98.0, 100.0],
        "Volume": [1e6, 1.2e6],
    }))
    return m


@pytest.mark.asyncio
async def test_matrix_row_with_memory(market):
    item = WatchlistItem(ticker="VRT", company_name="Vertiv")
    memory = InvestmentMemoryRecord(
        ticker="VRT",
        thesis="test",
        confidence=0.8,
        scenario="base",
        expected_outcome="up",
        recommendation="buy",
        scores={"news_agent": 10, "technical_agent": 25, "sentiment_agent": 5},
    )
    svc = WatchlistMatrixService(market)
    rows = await svc.build([item], {"VRT": memory})
    assert len(rows) == 1
    assert rows[0].ticker == "VRT"
    assert rows[0].recommendation == "buy"
    assert rows[0].change_pct == pytest.approx(2.04, rel=0.1)
    assert rows[0].technical_score == 25
