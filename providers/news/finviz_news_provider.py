"""Finviz.com news provider — ticker page + market news feed."""

from __future__ import annotations

from domain.enums import ImpactLevel, NewsSentiment, TimeHorizon
from domain.reports import NewsItem
from providers.finviz.client import FinvizClient, get_finviz_client
from providers.interfaces import NewsProvider
from providers.news.intelligence import enrich_news_item
from utils.logging import get_logger

logger = get_logger(__name__)


class FinvizNewsProvider(NewsProvider):
    name = "finviz"

    def __init__(self, client: FinvizClient | None = None) -> None:
        self._client = client or get_finviz_client()

    def _to_items(self, rows: list[dict]) -> list[NewsItem]:
        out: list[NewsItem] = []
        for row in rows:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            item = enrich_news_item(
                NewsItem(
                    title=title,
                    source=str(row.get("source") or "Finviz"),
                    url=row.get("url"),
                    published_at=row.get("published_at"),
                    snippet=None,
                    sentiment=NewsSentiment.NEUTRAL,
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.WEEKLY,
                    related_tickers=list(row.get("related_tickers") or []),
                )
            )
            out.append(item)
        return out

    async def get_company_news(self, ticker: str, max_results: int = 20) -> list[NewsItem]:
        try:
            rows = await self._client.get_ticker_news(ticker.upper(), max_results=max_results)
            return self._to_items(rows)[:max_results]
        except Exception as exc:
            logger.warning("finviz.news.ticker_failed", ticker=ticker, error=str(exc))
            return []

    async def search_news(
        self,
        query: str,
        max_results: int = 10,
        hint_category=None,
    ) -> list[NewsItem]:
        """Market news feed filtered by query tokens (Finviz has no search API)."""
        _ = hint_category
        try:
            rows = await self._client.get_market_news(max_results=max(40, max_results * 3))
        except Exception as exc:
            logger.warning("finviz.news.market_failed", error=str(exc))
            return []
        tokens = [t.lower() for t in (query or "").split() if len(t) > 2]
        if not tokens:
            return self._to_items(rows)[:max_results]
        filtered = [
            r
            for r in rows
            if any(tok in (r.get("title") or "").lower() for tok in tokens)
        ]
        use = filtered or rows
        return self._to_items(use)[:max_results]
