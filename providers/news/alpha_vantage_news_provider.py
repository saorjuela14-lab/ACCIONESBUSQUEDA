"""Alpha Vantage NEWS_SENTIMENT provider with historical windows."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from config.settings import get_settings
from domain.enums import ImpactLevel, NewsSentiment, TimeHorizon
from domain.reports import NewsItem
from providers.interfaces import NewsProvider
from providers.news.intelligence import enrich_news_item
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)

AV_BASE = "https://www.alphavantage.co/query"


def _parse_av_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _map_sentiment(score: float | None) -> NewsSentiment:
    if score is None:
        return NewsSentiment.NEUTRAL
    if score >= 0.15:
        return NewsSentiment.BULLISH
    if score <= -0.15:
        return NewsSentiment.BEARISH
    return NewsSentiment.NEUTRAL


class AlphaVantageNewsProvider(NewsProvider):
    """Historical and recent news via Alpha Vantage NEWS_SENTIMENT."""

    name = "alpha_vantage"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.alpha_vantage_api_key
        if not self._api_key:
            raise ValueError("ALPHA_VANTAGE_API_KEY is required")

    @async_retry
    async def _request(self, params: dict) -> dict:
        params = {**params, "apikey": self._api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(AV_BASE, params=params)
            response.raise_for_status()
            data = response.json()

        if "Error Message" in data:
            raise ValueError(data["Error Message"])
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information", "Rate limit")
            # Return empty feed on rate limit instead of failing entire analysis
            if "Thank you for using Alpha Vantage" in msg or "rate limit" in msg.lower():
                logger.warning("av.news.rate_limited", message=msg[:120])
                return {"feed": []}
            raise ValueError(f"Alpha Vantage limit: {msg}")

        return data

    def _parse_feed(self, feed: list[dict], ticker: str) -> list[NewsItem]:
        items: list[NewsItem] = []
        for article in feed:
            title = (article.get("title") or "").strip()
            if not title:
                continue

            ticker_score = None
            for ts in article.get("ticker_sentiment", []):
                if ts.get("ticker", "").upper() == ticker.upper():
                    try:
                        ticker_score = float(ts.get("ticker_sentiment_score", 0))
                    except (TypeError, ValueError):
                        ticker_score = None
                    break

            overall = article.get("overall_sentiment_score")
            try:
                overall_f = float(overall) if overall is not None else None
            except (TypeError, ValueError):
                overall_f = None

            sentiment = _map_sentiment(ticker_score if ticker_score is not None else overall_f)
            published_at = _parse_av_datetime(article.get("time_published", ""))

            items.append(
                enrich_news_item(
                    NewsItem(
                        title=title,
                        source=article.get("source", "Alpha Vantage"),
                        url=article.get("url"),
                        published_at=published_at,
                        snippet=(article.get("summary") or "")[:500] or None,
                        sentiment=sentiment,
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.WEEKLY,
                    )
                )
            )
        return items

    async def fetch_ticker_news_window(
        self,
        ticker: str,
        time_from: datetime,
        time_to: datetime | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        params: dict = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker.upper(),
            "time_from": time_from.strftime("%Y%m%dT%H%M"),
            "limit": str(limit),
            "sort": "LATEST",
        }
        if time_to:
            params["time_to"] = time_to.strftime("%Y%m%dT%H%M")

        data = await self._request(params)
        return self._parse_feed(data.get("feed", []), ticker.upper())

    async def fetch_timeline(self, ticker: str, limit_per_window: int = 50) -> dict[str, list[NewsItem]]:
        """Fetch recent (3m) and historical (2y excl. 3m) news windows."""
        now = datetime.now(timezone.utc)
        three_months_ago = now - timedelta(days=90)
        two_years_ago = now - timedelta(days=730)

        recent: list[NewsItem] = []
        historical: list[NewsItem] = []

        try:
            recent = await self.fetch_ticker_news_window(
                ticker, time_from=three_months_ago, limit=limit_per_window
            )
        except Exception as exc:
            logger.warning("av.news.recent_failed", ticker=ticker, error=str(exc))

        # Small pause to respect AV rate limits (market data may have consumed quota)
        await asyncio.sleep(2.5)

        try:
            historical = await self.fetch_ticker_news_window(
                ticker,
                time_from=two_years_ago,
                time_to=three_months_ago - timedelta(seconds=1),
                limit=limit_per_window,
            )
        except Exception as exc:
            logger.warning("av.news.historical_failed", ticker=ticker, error=str(exc))

        return {"recent_3m": recent, "historical_2y": historical}

    async def get_company_news(self, ticker: str, max_results: int = 20) -> list[NewsItem]:
        now = datetime.now(timezone.utc)
        items = await self.fetch_ticker_news_window(
            ticker, time_from=now - timedelta(days=90), limit=max_results
        )
        return items[:max_results]

    async def search_news(self, query: str, max_results: int = 10, hint_category=None) -> list[NewsItem]:
        return []
