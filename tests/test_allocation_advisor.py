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


@pytest.mark.asyncio
async def test_single_ticker_not_repeated_across_buckets(market_mock):
    """Each ticker must appear in at most one allocation bucket."""
    watchlist = [WatchlistItem(ticker="SEDG")]
    memory = {
        "SEDG": InvestmentMemoryRecord(
            ticker="SEDG", thesis="Solar play", reasons=[], scores={"news_agent": 15},
            confidence=0.7, scenario="base", expected_outcome="hold", recommendation="hold",
        ),
    }
    svc = MarketAllocationAdvisorService(market_mock)
    plan = await svc.advise(
        capital=50,
        watchlist=watchlist,
        memory_by_ticker=memory,
        market_regime="neutral",
        strategy_style="balanced",
    )

    all_bucket_tickers = [t for b in plan.buckets if b.key != "cash" for t in b.tickers]
    assert all_bucket_tickers.count("SEDG") <= 1
    item_tickers = [i.ticker for i in plan.items]
    assert item_tickers.count("SEDG") <= 1


@pytest.mark.asyncio
async def test_tickers_distributed_uniquely(market_mock):
    watchlist = [
        WatchlistItem(ticker="RVMD"),
        WatchlistItem(ticker="ABBV"),
        WatchlistItem(ticker="IONQ"),
    ]
    memory = {
        "RVMD": InvestmentMemoryRecord(
            ticker="RVMD", thesis="Biotech", reasons=[], scores={"news_agent": 25},
            confidence=0.8, scenario="base", expected_outcome="up", recommendation="buy",
        ),
        "ABBV": InvestmentMemoryRecord(
            ticker="ABBV", thesis="Pharma", reasons=[], scores={"fundamental_agent": 12},
            confidence=0.65, scenario="base", expected_outcome="hold", recommendation="hold",
        ),
        "IONQ": InvestmentMemoryRecord(
            ticker="IONQ", thesis="Quantum", reasons=[], scores={"technical_agent": 18},
            confidence=0.7, scenario="base", expected_outcome="up", recommendation="buy",
        ),
    }
    svc = MarketAllocationAdvisorService(market_mock)
    plan = await svc.advise(
        capital=5000,
        watchlist=watchlist,
        memory_by_ticker=memory,
        market_regime="neutral",
        strategy_style="balanced",
    )

    assigned = [t for b in plan.buckets if b.key != "cash" for t in b.tickers]
    assert len(assigned) == len(set(assigned)), f"Tickers duplicados entre buckets: {assigned}"
    assert len(plan.items) == len({i.ticker for i in plan.items})
