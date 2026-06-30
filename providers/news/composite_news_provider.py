"""Composite news provider: yfinance primary, DuckDuckGo supplemental."""

from domain.enums import NewsTopicCategory
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from providers.news.duckduckgo_provider import DuckDuckGoNewsProvider
from providers.news.intelligence import dedupe_news
from providers.news.yfinance_news_provider import YFinanceNewsProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class CompositeNewsProvider(NewsProvider):
    """Merges Yahoo Finance ticker news with targeted web search."""

    def __init__(
        self,
        primary: NewsProvider | None = None,
        supplemental: NewsProvider | None = None,
    ) -> None:
        self._primary = primary or YFinanceNewsProvider()
        self._supplemental = supplemental or DuckDuckGoNewsProvider()

    async def get_company_news(self, ticker: str, max_results: int = 20) -> list[NewsItem]:
        items: list[NewsItem] = []
        if hasattr(self._primary, "get_company_news"):
            items.extend(await self._primary.get_company_news(ticker, max_results=max_results))
        return items[:max_results]

    async def search_news(
        self,
        query: str,
        max_results: int = 10,
        hint_category: NewsTopicCategory | None = None,
    ) -> list[NewsItem]:
        return await self._supplemental.search_news(
            query, max_results=max_results, hint_category=hint_category
        )

    async def collect_company_intelligence(
        self,
        ticker: str,
        queries: list[tuple[NewsTopicCategory, str]],
        max_per_query: int = 3,
        max_ticker_news: int = 15,
    ) -> list[NewsItem]:
        items: list[NewsItem] = []

        try:
            items.extend(await self.get_company_news(ticker, max_results=max_ticker_news))
        except Exception as exc:
            logger.warning("news.primary.failed", ticker=ticker, error=str(exc))

        for category, query in queries:
            try:
                found = await self.search_news(query, max_results=max_per_query, hint_category=category)
                items.extend(found)
            except Exception as exc:
                logger.warning("news.supplemental.failed", query=query, error=str(exc))

        return dedupe_news(items)
