"""Composite sentiment provider aggregating all social sources."""

import asyncio

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.interfaces import SentimentProvider
from providers.sentiment.reddit_search_provider import RedditSearchProvider
from providers.sentiment.stocktwits_provider import StocktwitsProvider
from providers.sentiment.web_sentiment_provider import WebSentimentProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class CompositeSentimentProvider(SentimentProvider):
    """Aggregates Stocktwits + Reddit search + Seeking Alpha/Yahoo."""

    def __init__(
        self,
        stocktwits: StocktwitsProvider | None = None,
        reddit: RedditSearchProvider | None = None,
        web: WebSentimentProvider | None = None,
    ) -> None:
        self._providers: list[SentimentProvider] = [
            stocktwits or StocktwitsProvider(),
            reddit or RedditSearchProvider(),
            web or WebSentimentProvider(),
        ]

    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        results = await asyncio.gather(
            *[p.get_sentiment(ticker, company_name) for p in self._providers],
            return_exceptions=True,
        )

        all_items: list[SentimentItem] = []
        sources: list[str] = []
        bullish = bearish = neutral = 0
        scores: list[float] = []
        stocktwits_bullish_pct: float | None = None

        for result in results:
            if isinstance(result, Exception):
                logger.warning("sentiment.provider.failed", error=str(result))
                continue
            all_items.extend(result.items)
            sources.extend(result.sources)
            bullish += result.bullish_count
            bearish += result.bearish_count
            neutral += result.neutral_count
            scores.append(result.score)
            if result.stocktwits_bullish_pct is not None:
                stocktwits_bullish_pct = result.stocktwits_bullish_pct

        avg_score = sum(scores) / len(scores) if scores else 0.0

        return SentimentSnapshot(
            ticker=ticker.upper(),
            items=all_items,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            sources=list(dict.fromkeys(sources)),
            stocktwits_bullish_pct=stocktwits_bullish_pct,
            score=avg_score,
        )
