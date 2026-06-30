"""Stocktwits public API sentiment provider."""

import asyncio
from datetime import datetime

import httpx

from domain.enums import NewsSentiment
from domain.sentiment import SentimentItem, SentimentSnapshot
from providers.interfaces import SentimentProvider
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)

STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"


class StocktwitsProvider(SentimentProvider):
    name = "stocktwits"

    @async_retry
    async def _fetch_stream(self, ticker: str) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{STOCKTWITS_BASE}/streams/symbol/{ticker.upper()}.json")
            response.raise_for_status()
            return response.json()

    def _parse_sentiment(self, message: dict) -> NewsSentiment:
        entities = message.get("entities", {}) or {}
        basic = (entities.get("sentiment") or {}).get("basic", "").lower()
        if basic == "bullish":
            return NewsSentiment.BULLISH
        if basic == "bearish":
            return NewsSentiment.BEARISH
        return NewsSentiment.NEUTRAL

    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        items: list[SentimentItem] = []
        bullish = bearish = neutral = 0

        try:
            data = await self._fetch_stream(ticker)
            for msg in data.get("messages", [])[:30]:
                sentiment = self._parse_sentiment(msg)
                if sentiment == NewsSentiment.BULLISH:
                    bullish += 1
                elif sentiment == NewsSentiment.BEARISH:
                    bearish += 1
                else:
                    neutral += 1

                created = msg.get("created_at")
                published = None
                if created:
                    try:
                        published = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    except ValueError:
                        published = None

                items.append(
                    SentimentItem(
                        source="stocktwits",
                        text=msg.get("body", ""),
                        url=f"https://stocktwits.com/symbol/{ticker.upper()}",
                        sentiment=sentiment,
                        author=(msg.get("user") or {}).get("username"),
                        engagement=(msg.get("likes") or {}).get("total"),
                        published_at=published,
                    )
                )
        except Exception as exc:
            logger.warning("stocktwits.fetch.failed", ticker=ticker, error=str(exc))

        total_labeled = bullish + bearish
        bullish_pct = (bullish / total_labeled * 100) if total_labeled else None
        score = ((bullish - bearish) / max(len(items), 1)) * 50

        return SentimentSnapshot(
            ticker=ticker.upper(),
            items=items,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            sources=["stocktwits"] if items else [],
            stocktwits_bullish_pct=round(bullish_pct, 1) if bullish_pct is not None else None,
            score=score,
        )
