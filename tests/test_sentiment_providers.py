"""Tests for social sentiment providers."""

from unittest.mock import AsyncMock, patch

import pytest

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.sentiment.composite_sentiment_provider import CompositeSentimentProvider
from providers.sentiment.stocktwits_provider import StocktwitsProvider


@pytest.mark.asyncio
async def test_stocktwits_parses_messages():
    provider = StocktwitsProvider()
    mock_data = {
        "messages": [
            {
                "body": "AAPL to the moon bullish",
                "created_at": "2025-01-01T12:00:00Z",
                "entities": {"sentiment": {"basic": "Bullish"}},
                "user": {"username": "trader1"},
                "likes": {"total": 5},
            },
            {
                "body": "Selling AAPL bearish",
                "created_at": "2025-01-01T13:00:00Z",
                "entities": {"sentiment": {"basic": "Bearish"}},
                "user": {"username": "trader2"},
                "likes": {"total": 2},
            },
        ]
    }

    with patch.object(provider, "_fetch_stream", new_callable=AsyncMock, return_value=mock_data):
        snapshot = await provider.get_sentiment("AAPL")

    assert len(snapshot.items) == 2
    assert snapshot.bullish_count == 1
    assert snapshot.bearish_count == 1
    assert snapshot.sources == ["stocktwits"]


@pytest.mark.asyncio
async def test_composite_aggregates_sources():
    stocktwits = AsyncMock()
    stocktwits.get_sentiment.return_value = SentimentSnapshot(
        ticker="AAPL",
        items=[SentimentItem(source="stocktwits", text="bullish", sentiment=NewsSentiment.BULLISH)],
        bullish_count=1,
        sources=["stocktwits"],
        score=20,
    )
    reddit = AsyncMock()
    reddit.get_sentiment.return_value = SentimentSnapshot(
        ticker="AAPL",
        items=[SentimentItem(source="reddit", text="bearish", sentiment=NewsSentiment.BEARISH)],
        bearish_count=1,
        sources=["reddit"],
        score=-15,
    )
    web = AsyncMock()
    web.get_sentiment.return_value = SentimentSnapshot(ticker="AAPL", sources=[], score=0)

    provider = CompositeSentimentProvider(stocktwits=stocktwits, reddit=reddit, web=web)
    snapshot = await provider.get_sentiment("AAPL")

    assert len(snapshot.items) == 2
    assert "stocktwits" in snapshot.sources
    assert "reddit" in snapshot.sources
    assert snapshot.bullish_count == 1
    assert snapshot.bearish_count == 1
