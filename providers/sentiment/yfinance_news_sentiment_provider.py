"""Yahoo Finance news headline sentiment — no DuckDuckGo / rate limits."""

import asyncio

import yfinance as yf

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.interfaces import SentimentProvider
from providers.news.intelligence import classify_sentiment
from utils.logging import get_logger

logger = get_logger(__name__)


class YFinanceNewsSentimentProvider(SentimentProvider):
    """Derives sentiment from Yahoo Finance news headlines and summaries."""

    name = "yfinance_news"

    def _fetch(self, ticker: str) -> list[dict]:
        try:
            return yf.Ticker(ticker).news or []
        except Exception as exc:
            logger.warning("yfinance.sentiment.fetch_failed", ticker=ticker, error=str(exc))
            return []

    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        raw = await asyncio.to_thread(self._fetch, ticker.upper())
        items: list[SentimentItem] = []
        bullish = bearish = neutral = 0

        for entry in raw[:20]:
            content = entry.get("content") or entry
            title = (content.get("title") or "").strip()
            summary = (content.get("summary") or content.get("description") or "").strip()
            if not title:
                continue

            text = f"{title} {summary}"
            sentiment = classify_sentiment(text)
            if sentiment == NewsSentiment.BULLISH:
                bullish += 1
            elif sentiment == NewsSentiment.BEARISH:
                bearish += 1
            else:
                neutral += 1

            url = content.get("canonicalUrl") or content.get("clickThroughUrl")
            if isinstance(url, dict):
                url = url.get("url")

            items.append(
                SentimentItem(
                    source="yfinance_news",
                    text=text[:400],
                    url=url,
                    sentiment=sentiment,
                )
            )

        score = ((bullish - bearish) / max(len(items), 1)) * 45
        return SentimentSnapshot(
            ticker=ticker.upper(),
            items=items,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            sources=["yfinance_news"] if items else [],
            score=score,
        )
