"""HTF weekly+monthly uptrend gate."""

from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

from services.htf_trend_filter import HtfTrendFilter


def _zigzag_uptrend(n: int = 120, freq: str = "W") -> pd.DataFrame:
    idx = pd.date_range("2018-01-01", periods=n, freq=freq)
    close = []
    p = 20.0
    for i in range(n):
        if i % 8 == 0:
            p += 3
        elif i % 8 == 4:
            p -= 1
        else:
            p += 0.4
        close.append(p)
    close = np.array(close)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.5,
            "Low": close - 1.5,
            "Close": close,
            "Volume": np.full(n, 1e6),
        },
        index=idx,
    )


def _downtrend_ohlc(n: int = 90) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="ME")
    close = 80 - np.linspace(0, 50, n)
    return pd.DataFrame(
        {
            "Open": close + 0.2,
            "High": close + 1.2,
            "Low": close - 1.5,
            "Close": close,
            "Volume": np.full(n, 1e6),
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_htf_pass_both_uptrends():
    market = AsyncMock()
    up = _zigzag_uptrend()
    market.get_history = AsyncMock(return_value=up)
    filt = HtfTrendFilter(market, min_confidence=0.5)
    result = await filt.evaluate("VRT")
    assert result.passed is True
    assert result.weekly == "uptrend"
    assert result.monthly == "uptrend"


@pytest.mark.asyncio
async def test_htf_reject_when_monthly_down():
    market = AsyncMock()

    async def _hist(ticker, period="1y", interval="1d"):
        if interval in ("1mo", "1month", "month"):
            return _downtrend_ohlc()
        return _zigzag_uptrend()

    market.get_history = AsyncMock(side_effect=_hist)
    filt = HtfTrendFilter(market, min_confidence=0.5)
    result = await filt.evaluate("XYZ")
    assert result.passed is False
