"""Tests for composite market data provider fallback chain."""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from providers.market.composite_market_provider import CompositeMarketDataProvider
from providers.market.factory import get_market_provider, reset_market_provider
from providers.market.rate_limit_tracker import get_rate_limit_tracker


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        {"Open": [100], "High": [101], "Low": [99], "Close": [100.5], "Volume": [1000]},
        index=pd.to_datetime(["2025-01-01"]),
    )


@pytest.fixture
def sample_quote():
    return {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "current_price": 150.0,
        "sector": "Technology",
        "source": "test",
    }


@pytest.mark.asyncio
async def test_composite_falls_back_on_polygon_failure(sample_df, sample_quote):
    polygon = AsyncMock()
    polygon.get_history.side_effect = ValueError("Polygon error")
    polygon.get_quote.side_effect = ValueError("Polygon error")

    alpha = AsyncMock()
    alpha.get_history.return_value = sample_df
    alpha.get_quote.return_value = sample_quote

    yfinance = AsyncMock()

    provider = CompositeMarketDataProvider(polygon=polygon, alpha_vantage=alpha, yfinance=yfinance)

    df = await provider.get_history("AAPL", period="5d", interval="5m")
    assert not df.empty
    alpha.get_history.assert_called_once()

    quote = await provider.get_quote("AAPL")
    assert quote["current_price"] == 150.0
    alpha.get_quote.assert_called_once()


@pytest.mark.asyncio
async def test_composite_skips_exhausted_provider(sample_df):
    tracker = get_rate_limit_tracker()
    tracker.reset()
    for _ in range(25):
        tracker.record("alpha_vantage")

    polygon = AsyncMock()
    polygon.get_history.side_effect = ValueError("fail")

    alpha = AsyncMock()
    alpha.get_history.return_value = sample_df

    yfinance = MagicMock()
    yfinance.get_history = AsyncMock(return_value=sample_df)

    provider = CompositeMarketDataProvider(polygon=polygon, alpha_vantage=alpha, yfinance=yfinance)
    provider._alpha_daily = 25

    df = await provider.get_history("AAPL", period="1d", interval="1d")
    assert not df.empty
    alpha.get_history.assert_not_called()
    yfinance.get_history.assert_called_once()


@pytest.mark.asyncio
async def test_factory_builds_composite():
    reset_market_provider()
    with patch("providers.market.factory.get_settings") as mock_settings:
        mock_settings.return_value.polygon_api_key = "poly_key"
        mock_settings.return_value.alpha_vantage_api_key = "av_key"
        mock_settings.return_value.yfinance_enabled = True
        provider = get_market_provider()
        from providers.market.composite_market_provider import CompositeMarketDataProvider

        assert isinstance(provider, CompositeMarketDataProvider)
    reset_market_provider()
