"""Seeking Alpha and Yahoo Finance sentiment via web search."""

import asyncio

from duckduckgo_search import DDGS

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.interfaces import SentimentProvider
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_POSITIVE = {"buy", "bullish", "outperform", "upgrade", "strong buy", "beat", "growth", "positive"}
_NEGATIVE = {"sell", "bearish", "underperform", "downgrade", "strong sell", "miss", "concern", "negative"}


class WebSentimentProvider(SentimentProvider):
    """Searches Seeking Alpha and Yahoo Finance for investor opinions."""

    name = "web_sentiment"

    SITES = (
        ("seeking_alpha", "site:seekingalpha.com"),
        ("yahoo_finance", "site:finance.yahoo.com"),
    )

    @sync_retry
    def _search(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.news(query, max_results=max_results):
                results.append(
                    {
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                    }
                )
            if not results:
                for item in ddgs.text(query, max_results=max_results):
                    results.append(
                        {
                            "title": item.get("title", ""),
                            "body": item.get("body", ""),
                            "url": item.get("href", ""),
                        }
                    )
        return results

    def _classify(self, text: str) -> NewsSentiment:
        lower = text.lower()
        pos = sum(1 for w in _POSITIVE if w in lower)
        neg = sum(1 for w in _NEGATIVE if w in lower)
        if pos > neg:
            return NewsSentiment.BULLISH
        if neg > pos:
            return NewsSentiment.BEARISH
        return NewsSentiment.NEUTRAL

    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        items: list[SentimentItem] = []
        bullish = bearish = neutral = 0
        sources_used: set[str] = set()
        seen: set[str] = set()

        for source_name, site_filter in self.SITES:
            query = f"{site_filter} {ticker} {company_name or ''} stock opinion".strip()
            try:
                results = await asyncio.to_thread(self._search, query, 4)
                for r in results:
                    url = r.get("url", "")
                    if url in seen:
                        continue
                    seen.add(url)

                    text = r.get("title") or r.get("body", "")
                    if not text:
                        continue

                    sentiment = self._classify(text)
                    if sentiment == NewsSentiment.BULLISH:
                        bullish += 1
                    elif sentiment == NewsSentiment.BEARISH:
                        bearish += 1
                    else:
                        neutral += 1

                    sources_used.add(source_name)
                    items.append(
                        SentimentItem(
                            source=source_name,
                            text=text[:500],
                            url=url,
                            sentiment=sentiment,
                        )
                    )
            except Exception as exc:
                logger.warning("web.sentiment.failed", source=source_name, error=str(exc))

        score = ((bullish - bearish) / max(len(items), 1)) * 35
        return SentimentSnapshot(
            ticker=ticker.upper(),
            items=items,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            sources=list(sources_used),
            score=score,
        )
