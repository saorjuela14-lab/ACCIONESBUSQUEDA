"""DuckDuckGo news provider."""

import asyncio
from datetime import datetime

from duckduckgo_search import DDGS

from domain.enums import ImpactLevel, NewsSentiment, TimeHorizon
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_POSITIVE = {"beat", "surge", "rally", "upgrade", "growth", "profit", "buy", "bullish", "record"}
_NEGATIVE = {"miss", "decline", "drop", "loss", "downgrade", "sell", "bearish", "lawsuit", "investigation"}


class DuckDuckGoNewsProvider(NewsProvider):
    @sync_retry
    def _search(self, query: str, max_results: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        with DDGS() as ddgs:
            for result in ddgs.news(query, max_results=max_results):
                title = result.get("title", "")
                text = title.lower()
                pos = sum(1 for w in _POSITIVE if w in text)
                neg = sum(1 for w in _NEGATIVE if w in text)
                if pos > neg:
                    sentiment = NewsSentiment.BULLISH
                elif neg > pos:
                    sentiment = NewsSentiment.BEARISH
                else:
                    sentiment = NewsSentiment.NEUTRAL

                published = result.get("date")
                published_at = None
                if published:
                    try:
                        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
                    except ValueError:
                        published_at = None

                items.append(
                    NewsItem(
                        title=title,
                        source=result.get("source", "unknown"),
                        url=result.get("url"),
                        published_at=published_at,
                        sentiment=sentiment,
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.WEEKLY,
                    )
                )
        return items

    async def search_news(self, query: str, max_results: int = 10) -> list[NewsItem]:
        try:
            return await asyncio.to_thread(self._search, query, max_results)
        except Exception as exc:
            logger.warning("news.search.failed", query=query, error=str(exc))
            return []
