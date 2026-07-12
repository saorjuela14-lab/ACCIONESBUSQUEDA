"""Tests for technical chart service."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.technical_chart_service import TechnicalChartService


def _ohlcv(rows: int = 80) -> pd.DataFrame:
    import math
    closes = [100 + math.sin(i / 5) * 5 + i * 0.1 for i in range(rows)]
    return pd.DataFrame({
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 5000 for i in range(rows)],
    }, index=pd.date_range("2025-01-01", periods=rows, freq="D"))


@pytest.mark.asyncio
async def test_technical_chart_builds_indicators():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=_ohlcv())

    svc = TechnicalChartService(market)
    data = await svc.build("AAPL", period="6mo")

    assert data.ticker == "AAPL"
    assert len(data.points) >= 30
    assert any(p.rsi is not None for p in data.points)
    assert any(p.sma20 is not None for p in data.points)
    assert data.snapshot is not None
    assert data.snapshot.price is not None
    assert data.summary


@pytest.mark.asyncio
async def test_technical_chart_insufficient_data():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=pd.DataFrame())

    svc = TechnicalChartService(market)
    data = await svc.build("XYZ")

    assert data.points == []
    assert "insuficientes" in data.summary.lower()


@pytest.mark.asyncio
async def test_technical_chart_intraday_timeframe():
    market = MagicMock()
    hourly = _ohlcv(60)
    hourly.index = pd.date_range("2025-01-02 09:30", periods=60, freq="h")
    market.get_history = AsyncMock(return_value=hourly)

    svc = TechnicalChartService(market)
    data = await svc.build("AAPL", chart_timeframe="1H")

    assert data.chart_timeframe == "1H"
    assert len(data.points) >= 20
    assert " " in data.points[0].date
    assert data.gaps is not None


@pytest.mark.asyncio
async def test_technical_chart_invalid_timeframe_defaults_daily():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=_ohlcv())

    svc = TechnicalChartService(market)
    data = await svc.build("AAPL", chart_timeframe="INVALID")

    assert data.chart_timeframe == "1D"
