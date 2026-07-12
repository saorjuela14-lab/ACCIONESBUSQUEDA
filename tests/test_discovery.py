"""Tests for company discovery pipeline."""

from unittest.mock import AsyncMock, patch

import pytest

from domain.discovery import DiscoveryMention
from domain.enums import InvestmentRecommendation
from domain.reports import InvestmentThesis, ScenarioCase
from providers.discovery.ticker_extractor import extract_tickers
from services.company_discovery_service import CompanyDiscoveryService


def test_extract_tickers_cash_and_bare():
    text = "Looking at $AAPL and MSFT for growth. CEO said buy NVDA."
    tickers = extract_tickers(text)
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "NVDA" in tickers
    assert "CEO" not in tickers


def test_extract_tickers_blocklist_etfs():
    text = "SPY QQQ market ETF"
    tickers = extract_tickers(text)
    assert "SPY" not in tickers
    assert "QQQ" not in tickers


@pytest.mark.asyncio
async def test_discovery_research_ranks_candidates():
    market = AsyncMock()
    market.get_quote = AsyncMock(side_effect=lambda t: {
        "ticker": t,
        "company_name": f"Company {t}",
        "sector": "Technology",
        "current_price": 100.0,
    })

    st_mentions = [
        ("AAPL", DiscoveryMention(source="stocktwits", text="AAPL bullish", sentiment="bullish")),
        ("AAPL", DiscoveryMention(source="stocktwits", text="AAPL trending", sentiment="bullish")),
        ("TSLA", DiscoveryMention(source="stocktwits", text="TSLA watch", sentiment="neutral")),
    ]
    x_mentions = [
        ("AAPL", DiscoveryMention(source="x", text="$AAPL breakout", url="https://x.com/post/1")),
    ]
    reddit_mentions = [
        ("TSLA", DiscoveryMention(source="reddit", text="TSLA DD", url="https://reddit.com/r/wsb")),
        ("TSLA", DiscoveryMention(source="reddit", text="TSLA long", url="https://reddit.com/r/stocks")),
    ]
    news_mentions = [
        ("NVDA", DiscoveryMention(source="news", text="NVDA earnings beat", url="https://news.com/1")),
    ]

    service = CompanyDiscoveryService(market_provider=market)
    with patch.object(service._stocktwits, "scan", new_callable=AsyncMock, return_value=st_mentions), \
         patch.object(service._x, "scan", new_callable=AsyncMock, return_value=x_mentions), \
         patch.object(service._reddit, "scan", new_callable=AsyncMock, return_value=reddit_mentions), \
         patch.object(service._news, "scan", new_callable=AsyncMock, return_value=news_mentions):
        report = await service.research(themes=["tech"], max_candidates=10)

    assert len(report.candidates) == 3
    tickers = [c.ticker for c in report.candidates]
    assert "AAPL" in tickers
    assert "TSLA" in tickers
    assert "NVDA" in tickers
    assert report.candidates[0].ticker == "AAPL"
    assert report.candidates[0].mention_count >= 2
    assert "stocktwits" in report.candidates[0].sources
    assert report.sources_scanned == ["stocktwits", "x", "reddit", "news"]


@pytest.mark.asyncio
async def test_discovery_excludes_watchlist_tickers():
    market = AsyncMock()
    market.get_quote = AsyncMock(return_value={
        "ticker": "AAPL",
        "company_name": "Apple Inc",
        "current_price": 180.0,
    })

    mentions = [("AAPL", DiscoveryMention(source="stocktwits", text="AAPL"))]

    service = CompanyDiscoveryService(market_provider=market)
    with patch.object(service._stocktwits, "scan", new_callable=AsyncMock, return_value=mentions), \
         patch.object(service._x, "scan", new_callable=AsyncMock, return_value=[]), \
         patch.object(service._reddit, "scan", new_callable=AsyncMock, return_value=[]), \
         patch.object(service._news, "scan", new_callable=AsyncMock, return_value=[]):
        report = await service.research(exclude_tickers=["AAPL"])

    assert len(report.candidates) == 0


@pytest.mark.asyncio
async def test_discovery_analyze_calls_committee():
    market = AsyncMock()
    market.get_quote = AsyncMock(return_value={
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "current_price": 900.0,
    })

    mentions = [("NVDA", DiscoveryMention(source="news", text="NVDA surge"))]

    case = ScenarioCase(name="Base", probability=0.5, thesis="test", confidence=0.7)
    mock_thesis = InvestmentThesis(
        ticker="NVDA",
        executive_summary="NVIDIA shows strong momentum.",
        investment_thesis="test",
        bull_case=case,
        bear_case=case,
        base_case=case,
        recommendation=InvestmentRecommendation.BUY,
        confidence=0.75,
    )

    analysis = AsyncMock()
    analysis.analyze_ticker = AsyncMock(return_value=mock_thesis)

    service = CompanyDiscoveryService(market_provider=market, analysis_service=analysis)
    with patch.object(service._stocktwits, "scan", new_callable=AsyncMock, return_value=mentions), \
         patch.object(service._x, "scan", new_callable=AsyncMock, return_value=[]), \
         patch.object(service._reddit, "scan", new_callable=AsyncMock, return_value=[]), \
         patch.object(service._news, "scan", new_callable=AsyncMock, return_value=[]):
        result = await service.research_and_analyze(analyze_top=1)

    assert len(result.analyses) == 1
    assert result.analyses[0].ticker == "NVDA"
    assert "NVDA" in result.recommendation_summary
    analysis.analyze_ticker.assert_called_once()
