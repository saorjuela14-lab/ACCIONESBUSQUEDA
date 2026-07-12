"""Market allocation advisor tests."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from domain.entities import InvestmentMemoryRecord, WatchlistItem
from services.market_allocation_advisor_service import MarketAllocationAdvisorService


@pytest.fixture
def market_mock():
    m = MagicMock()
    m.get_quote = AsyncMock(
        side_effect=lambda t: {
            "ticker": t,
            "company_name": f"Co {t}",
            "current_price": 100.0,
            "market_cap": 5e9 if t in ("RVMD", "IONQ") else 50e9,
            "sector": "Biotechnology" if t in ("RVMD", "IONQ") else "Healthcare",
            "revenueGrowth": 0.35 if t == "RVMD" else 0.05,
        }
    )
    hist = pd.DataFrame({"Close": [98, 99, 100, 101, 102]})
    m.get_history = AsyncMock(return_value=hist)
    return m


@pytest.mark.asyncio
async def test_allocation_advise_returns_buckets(market_mock):
    watchlist = [
        WatchlistItem(ticker="RVMD"),
        WatchlistItem(ticker="ABBV"),
        WatchlistItem(ticker="IONQ"),
    ]
    memory = {
        "RVMD": InvestmentMemoryRecord(
            ticker="RVMD", thesis="Biotech growth", reasons=[], scores={"news_agent": 20},
            confidence=0.75, scenario="base", expected_outcome="upside", recommendation="buy",
        ),
        "ABBV": InvestmentMemoryRecord(
            ticker="ABBV", thesis="Stable pharma", reasons=[], scores={"fundamental_agent": 10},
            confidence=0.6, scenario="base", expected_outcome="hold", recommendation="hold",
        ),
    }
    svc = MarketAllocationAdvisorService(market_mock)
    plan = await svc.advise(
        capital=5000,
        watchlist=watchlist,
        memory_by_ticker=memory,
        market_regime="neutral",
        market_regime_score=0.1,
        strategy_style="balanced",
    )
    assert plan.capital == 5000
    assert len(plan.buckets) == 4
    total_pct = sum(b.allocation_pct for b in plan.buckets)
    assert 99 <= total_pct <= 101
    assert any(b.key == "emerging" for b in plan.buckets)
    assert len(plan.items) >= 1
    assert "5000" in plan.summary or "5,000" in plan.summary
