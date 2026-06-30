"""Reddit sentiment via web search (no OAuth required)."""

import asyncio
import re

from duckduckgo_search import DDGS

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.interfaces import SentimentProvider
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_SUBREDDITS = ("stocks", "wallstreetbets", "investing", "StockMarket", "options")
_POSITIVE = {"buy", "bullish", "calls", "moon", "undervalued", "beat", "upgrade", "long"}
_NEGATIVE = {"sell", "bearish", "puts", "overvalued", "miss", "downgrade", "short", "crash"}


class RedditSearchProvider(SentimentProvider):
    """Collects Reddit discussions via DuckDuckGo site:reddit.com search."""

    name = "reddit_search"

    @sync_retry
    def _search(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = item.get("href", "")
                if "reddit.com" not in url:
                    continue
                results.append(
                    {
                        "title": item.get("title", ""),
                        "body": item.get("body", ""),
                        "url": url,
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

    def _extract_subreddit(self, url: str) -> str | None:
        match = re.search(r"reddit\.com/r/([^/]+)", url)
        return match.group(1) if match else None

    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        items: list[SentimentItem] = []
        bullish = bearish = neutral = 0
        seen_urls: set[str] = set()

        queries = [
            f"site:reddit.com/r/stocks {ticker} stock",
            f"site:reddit.com/r/wallstreetbets {ticker}",
            f"site:reddit.com {company_name or ticker} sentiment",
        ]

        for query in queries:
            try:
                results = await asyncio.to_thread(self._search, query, 5)
                for r in results:
                    url = r.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    text = f"{r.get('title', '')} {r.get('body', '')}".strip()
                    if not text:
                        continue

                    sentiment = self._classify(text)
                    if sentiment == NewsSentiment.BULLISH:
                        bullish += 1
                    elif sentiment == NewsSentiment.BEARISH:
                        bearish += 1
                    else:
                        neutral += 1

                    items.append(
                        SentimentItem(
                            source="reddit",
                            text=text[:500],
                            url=url,
                            sentiment=sentiment,
                            author=self._extract_subreddit(url),
                        )
                    )
            except Exception as exc:
                logger.warning("reddit.search.failed", query=query, error=str(exc))

        score = ((bullish - bearish) / max(len(items), 1)) * 40
        return SentimentSnapshot(
            ticker=ticker.upper(),
            items=items,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            sources=["reddit"] if items else [],
            score=score,
        )
