"""Correlation service tests."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.correlation_service import CorrelationService


def _price_series(n: int = 100, start: float = 100.0, drift: float = 0.001) -> pd.DataFrame:
    import numpy as np

    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + drift + np.random.randn() * 0.01))
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"Close": prices}, index=idx)


@pytest.fixture
def mock_market():
    market = MagicMock()
    market.get_quote = AsyncMock(
        return_value={
            "ticker": "ABBV",
            "company_name": "AbbVie Inc.",
            "sector": "Healthcare",
            "industry": "Drug Manufacturers—General",
            "current_price": 250.0,
        }
    )
    market.get_peers = AsyncMock(return_value=["LLY", "PFE"])
    market.get_history = AsyncMock(return_value=_price_series())
    return market


@pytest.mark.asyncio
async def test_correlation_service_returns_report(mock_market):
    svc = CorrelationService(mock_market)
    report = await svc.analyze("ABBV")
    assert report.ticker == "ABBV"
    assert report.sector == "Healthcare"
    assert report.summary
    assert isinstance(report.benchmark_correlations, list)


@pytest.mark.asyncio
async def test_correlation_includes_macro_sensitivities(mock_market):
    svc = CorrelationService(mock_market)
    report = await svc.analyze("ABBV")
    assert len(report.macro_sensitivities) >= 1
