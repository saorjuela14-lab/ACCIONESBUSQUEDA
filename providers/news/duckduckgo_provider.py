"""DuckDuckGo news provider with web text fallback."""

import asyncio
from datetime import datetime

from duckduckgo_search import DDGS

from domain.enums import ImpactLevel, NewsSentiment, NewsTopicCategory, TimeHorizon
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from providers.news.intelligence import classify_sentiment, enrich_news_item
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)


class DuckDuckGoNewsProvider(NewsProvider):
    @sync_retry
    def _search_news(self, query: str, max_results: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        with DDGS() as ddgs:
            for result in ddgs.news(query, max_results=max_results):
                title = result.get("title", "")
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
                        sentiment=NewsSentiment.NEUTRAL,
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.WEEKLY,
                    )
                )
        return items

    @sync_retry
    def _search_text(self, query: str, max_results: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=max_results):
                title = result.get("title", "")
                body = result.get("body", "")
                if not title:
                    continue
                items.append(
                    NewsItem(
                        title=title,
                        source="web",
                        url=result.get("href"),
                        snippet=body[:300] if body else None,
                        sentiment=classify_sentiment(f"{title} {body}"),
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.MONTHLY,
                    )
                )
        return items

    def _search_combined(self, query: str, max_results: int, hint_category: NewsTopicCategory | None) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            items.extend(self._search_news(query, max_results))
        except Exception as exc:
            logger.warning("news.search.news_failed", query=query, error=str(exc))

        if len(items) < max_results:
            try:
                remaining = max_results - len(items)
                items.extend(self._search_text(query, remaining))
            except Exception as exc:
                logger.warning("news.search.text_failed", query=query, error=str(exc))

        return [enrich_news_item(item, hint_category=hint_category) for item in items]

    async def search_news(
        self,
        query: str,
        max_results: int = 10,
        hint_category: NewsTopicCategory | None = None,
    ) -> list[NewsItem]:
        try:
            return await asyncio.to_thread(self._search_combined, query, max_results, hint_category)
        except Exception as exc:
            logger.warning("news.search.failed", query=query, error=str(exc))
            return []
