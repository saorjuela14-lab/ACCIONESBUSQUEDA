"""Yahoo Finance news provider via yfinance."""

import asyncio
from datetime import datetime

import yfinance as yf

from domain.enums import ImpactLevel, NewsSentiment, TimeHorizon
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from providers.news.intelligence import enrich_news_item
from utils.logging import get_logger

logger = get_logger(__name__)


class YFinanceNewsProvider(NewsProvider):
    """Primary news source — recent company headlines with summaries from Yahoo Finance."""

    name = "yfinance"

    def _fetch_ticker_news(self, ticker: str, max_results: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            raw = yf.Ticker(ticker).news or []
        except Exception as exc:
            logger.warning("yfinance.news.fetch_failed", ticker=ticker, error=str(exc))
            return []

        for entry in raw[:max_results]:
            content = entry.get("content") or entry
            title = (content.get("title") or "").strip()
            if not title:
                continue

            summary = (content.get("summary") or content.get("description") or "").strip()
            pub = content.get("pubDate") or content.get("displayTime")
            published_at = None
            if pub:
                try:
                    published_at = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
                except ValueError:
                    published_at = None

            provider = content.get("provider") or {}
            source = provider.get("displayName") if isinstance(provider, dict) else "Yahoo Finance"
            if not source:
                source = "Yahoo Finance"

            url = content.get("canonicalUrl") or content.get("clickThroughUrl")
            if isinstance(url, dict):
                url = url.get("url")

            items.append(
                enrich_news_item(
                    NewsItem(
                        title=title,
                        source=str(source),
                        url=url,
                        published_at=published_at,
                        snippet=summary[:400] if summary else None,
                        sentiment=NewsSentiment.NEUTRAL,
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.WEEKLY,
                    )
                )
            )

        return items

    async def get_company_news(self, ticker: str, max_results: int = 20) -> list[NewsItem]:
        return await asyncio.to_thread(self._fetch_ticker_news, ticker.upper(), max_results)

    async def search_news(
        self,
        query: str,
        max_results: int = 10,
        hint_category=None,
    ) -> list[NewsItem]:
        return []
