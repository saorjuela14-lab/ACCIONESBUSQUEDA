"""Tests for technical chart service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.technical_chart_service import TechnicalChartService


def _ohlcv(
    rows: int = 80,
    *,
    end: str | None = None,
    freq: str = "D",
) -> pd.DataFrame:
    import math

    end_ts = pd.Timestamp(end or datetime.now(timezone.utc).date().isoformat())
    idx = pd.date_range(end=end_ts, periods=rows, freq=freq)
    closes = [100 + math.sin(i / 5) * 5 + i * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000_000 + i * 5000 for i in range(rows)],
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_technical_chart_builds_indicators():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=_ohlcv(rows=120))

    svc = TechnicalChartService(market)
    data = await svc.build("AAPL", period="6mo")

    assert data.ticker == "AAPL"
    assert len(data.points) >= 30
    assert any(p.rsi is not None for p in data.points)
    assert any(p.sma20 is not None for p in data.points)
    assert data.snapshot is not None
    assert data.snapshot.price is not None
    assert data.summary
    assert data.market_status == "live"
    assert data.as_of is not None


@pytest.mark.asyncio
async def test_technical_chart_insufficient_data():
    market = MagicMock()
    market.get_history = AsyncMock(return_value=pd.DataFrame())

    svc = TechnicalChartService(market)
    data = await svc.build("XYZ")

    assert data.points == []
    assert "insuficientes" in data.summary.lower()
    assert data.market_status == "unavailable"


@pytest.mark.asyncio
async def test_technical_chart_flags_delisted_stale_history():
    """NKLA-style: weekly bars ending months ago must not look like live market."""
    market = MagicMock()
    last = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    stale = _ohlcv(rows=100, end=last, freq="W-MON")
    # Pin last bar exactly so as_of is deterministic regardless of weekday align.
    stale.index = list(stale.index[:-1]) + [pd.Timestamp(last)]
    market.get_history = AsyncMock(return_value=stale)

    svc = TechnicalChartService(market)
    data = await svc.build("NKLA", period="6mo", chart_timeframe="1W")

    assert len(data.points) > 0
    assert data.market_status == "delisted"
    assert data.as_of == last
    assert data.stale_days is not None and data.stale_days > 90
    assert "deslistad" in data.summary.lower() or "sin cotización" in data.summary.lower()


@pytest.mark.asyncio
async def test_technical_chart_intraday_timeframe():
    market = MagicMock()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hourly = _ohlcv(60, end=now.isoformat(), freq="h")
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
    market.get_history = AsyncMock(return_value=_ohlcv(rows=120))

    svc = TechnicalChartService(market)
    data = await svc.build("AAPL", chart_timeframe="INVALID")

    assert data.chart_timeframe == "1D"
