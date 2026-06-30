"""Tests for FRED macro provider."""

from unittest.mock import AsyncMock, patch

import pytest

from providers.macro.fred_provider import FredProvider
from providers.macro.factory import get_macro_provider, reset_macro_provider


@pytest.fixture
def fred_observations():
    return {
        "observations": [
            {"date": "2025-01-01", "value": "5.33"},
            {"date": "2024-12-01", "value": "5.25"},
        ]
    }


@pytest.mark.asyncio
async def test_fred_provider_parses_series(fred_observations):
    provider = FredProvider(api_key="test_key")

    with patch.object(provider, "_get_observations", new_callable=AsyncMock) as mock_obs:
        mock_obs.return_value = fred_observations["observations"]
        result = await provider._fetch_series("FED_FUNDS")

    assert result is not None
    assert result["value"] == 5.33
    assert result["series_id"] == "FEDFUNDS"
    assert result["source"] == "fred"


@pytest.mark.asyncio
async def test_fred_provider_requires_api_key():
    with patch("providers.macro.fred_provider.get_settings") as mock_settings:
        mock_settings.return_value.fred_api_key = ""
        with pytest.raises(ValueError, match="FRED_API_KEY"):
            FredProvider(api_key="")


@pytest.mark.asyncio
async def test_factory_uses_fred_when_key_set():
    reset_macro_provider()
    with patch("providers.macro.factory.get_settings") as mock_settings:
        mock_settings.return_value.fred_api_key = "test_key"
        provider = get_macro_provider()
        from providers.macro.composite_macro_provider import CompositeMacroProvider

        assert isinstance(provider, CompositeMacroProvider)
    reset_macro_provider()


@pytest.mark.asyncio
async def test_factory_fallback_yfinance_only():
    reset_macro_provider()
    with patch("providers.macro.factory.get_settings") as mock_settings:
        mock_settings.return_value.fred_api_key = ""
        provider = get_macro_provider()
        from providers.macro.yfinance_macro_provider import YFinanceMacroProvider

        assert isinstance(provider, YFinanceMacroProvider)
    reset_macro_provider()
