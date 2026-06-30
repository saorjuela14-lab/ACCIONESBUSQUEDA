"""Composite news provider: Alpha Vantage timeline + yfinance + DuckDuckGo."""

from domain.enums import NewsTopicCategory
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from providers.news.alpha_vantage_news_provider import AlphaVantageNewsProvider
from providers.news.duckduckgo_provider import DuckDuckGoNewsProvider
from providers.news.intelligence import dedupe_news
from providers.news.temporal_analysis import NewsTimeline
from providers.news.yfinance_news_provider import YFinanceNewsProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class CompositeNewsProvider(NewsProvider):
    """Merges AV historical timeline, Yahoo Finance, and targeted web search."""

    def __init__(
        self,
        historical: NewsProvider | None = None,
        primary: NewsProvider | None = None,
        supplemental: NewsProvider | None = None,
    ) -> None:
        self._historical = historical or self._build_av_provider()
        self._primary = primary or YFinanceNewsProvider()
        self._supplemental = supplemental or DuckDuckGoNewsProvider()

    @staticmethod
    def _build_av_provider() -> AlphaVantageNewsProvider | None:
        try:
            return AlphaVantageNewsProvider()
        except ValueError:
            logger.warning("av.news.not_configured")
            return None

    async def get_company_news(self, ticker: str, max_results: int = 20) -> list[NewsItem]:
        items: list[NewsItem] = []
        if hasattr(self._primary, "get_company_news"):
            items.extend(await self._primary.get_company_news(ticker, max_results=max_results))
        return items[:max_results]

    async def fetch_timeline(self, ticker: str) -> NewsTimeline:
        timeline = NewsTimeline()

        if self._historical and hasattr(self._historical, "fetch_timeline"):
            try:
                windows = await self._historical.fetch_timeline(ticker)
                timeline.recent_3m = windows.get("recent_3m", [])
                timeline.historical_2y = windows.get("historical_2y", [])
            except Exception as exc:
                logger.warning("news.timeline.failed", ticker=ticker, error=str(exc))

        try:
            yf_news = await self.get_company_news(ticker, max_results=15)
            timeline.supplemental.extend(yf_news)
        except Exception as exc:
            logger.warning("news.yfinance.failed", ticker=ticker, error=str(exc))

        # Backfill windows when AV unavailable or filtered empty
        if not timeline.recent_3m and not timeline.historical_2y and timeline.supplemental:
            from providers.news.intelligence import partition_news_by_age

            recent, historical = partition_news_by_age(timeline.supplemental)
            timeline.recent_3m = recent
            timeline.historical_2y = historical
        elif timeline.supplemental:
            from providers.news.intelligence import dedupe_news, partition_news_by_age

            existing = dedupe_news(timeline.recent_3m + timeline.historical_2y)
            known_titles = {i.title.lower() for i in existing}
            extra = [i for i in timeline.supplemental if i.title.lower() not in known_titles]
            extra_recent, extra_hist = partition_news_by_age(extra)
            timeline.recent_3m = dedupe_news(timeline.recent_3m + extra_recent)
            timeline.historical_2y = dedupe_news(timeline.historical_2y + extra_hist)

        return timeline

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
        timeline = await self.fetch_timeline(ticker)

        for category, query in queries[:4]:
            try:
                found = await self.search_news(query, max_results=max_per_query, hint_category=category)
                timeline.supplemental.extend(found)
            except Exception as exc:
                logger.warning("news.supplemental.failed", query=query, error=str(exc))

        return dedupe_news(timeline.all_items)
