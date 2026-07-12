"""Tests for daily short-term trade recommendations."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from domain.daily_trade import DailyTradeReport, TradePick
from domain.discovery import DiscoveryCandidate, DiscoveryMention, DiscoveryReport
from services.daily_trade_recommendation_service import DailyTradeRecommendationService


def _make_hist(rows: int = 60, trend: float = 0.5) -> pd.DataFrame:
    data = {
        "Open": [100 + i * trend for i in range(rows)],
        "High": [101 + i * trend for i in range(rows)],
        "Low": [99 + i * trend for i in range(rows)],
        "Close": [100 + i * trend for i in range(rows)],
        "Volume": [1_000_000 + i * 10_000 for i in range(rows)],
    }
    return pd.DataFrame(data)


def _candidate(ticker: str, score: float = 10.0) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        ticker=ticker,
        company_name=f"Company {ticker}",
        score=score,
        mention_count=5,
        sources=["stocktwits", "news"],
        mentions=[DiscoveryMention(source="stocktwits", text=f"${ticker} breakout")],
        rationale=f"{ticker} trending",
        news_headlines=[f"{ticker} earnings beat"],
    )


@pytest.mark.asyncio
async def test_generate_ranks_picks_by_score():
    market = AsyncMock()
    market.get_quote = AsyncMock(return_value={
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "current_price": 900.0,
    })
    market.get_history = AsyncMock(return_value=_make_hist())

    discovery = AsyncMock()
    discovery.research = AsyncMock(return_value=DiscoveryReport(
        query_themes=["momentum"],
        candidates=[_candidate("NVDA", 12), _candidate("AMD", 8)],
        summary="test",
        sources_scanned=["stocktwits"],
    ))

    repo = AsyncMock()
    service = DailyTradeRecommendationService(market, discovery, repo)

    with patch.object(service, "_fetch_market_regime", new_callable=AsyncMock, return_value="bullish"):
        report = await service.generate(session="pre_market", max_picks=5, persist=True)

    assert isinstance(report, DailyTradeReport)
    assert report.market_regime == "bullish"
    assert report.session == "pre_market"
    assert len(report.picks) >= 1
    assert report.picks[0].ticker == "NVDA"
    repo.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_score_candidates_filtered():
    market = AsyncMock()
    market.get_quote = AsyncMock(return_value={"ticker": "XYZ", "current_price": 10.0})
    market.get_history = AsyncMock(return_value=pd.DataFrame({
        "Open": [10], "High": [10], "Low": [10], "Close": [10], "Volume": [100],
    }))

    discovery = AsyncMock()
    discovery.research = AsyncMock(return_value=DiscoveryReport(
        candidates=[_candidate("XYZ", 3)],
    ))

    service = DailyTradeRecommendationService(market, discovery, None)
    with patch.object(service, "_fetch_market_regime", new_callable=AsyncMock, return_value="neutral"):
        report = await service.generate(persist=False)

    assert len(report.picks) == 0


def test_momentum_score_high_on_strong_move():
    service = DailyTradeRecommendationService(MagicMock(), MagicMock())
    score = service._momentum_score(change_1d=4.0, change_5d=10.0, vol_spike=2.5, macd_hist=1.0)
    assert score >= 70


def test_classify_action_overbought():
    service = DailyTradeRecommendationService(MagicMock(), MagicMock())
    action, horizon = service._classify_action(3.0, 8.0, 80.0, 2.0)
    assert action == "vigilar"
    assert horizon == "Esperar pullback"
