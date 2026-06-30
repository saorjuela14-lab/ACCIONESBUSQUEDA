"""Watchlist matrix builder for CEO dashboard."""

from __future__ import annotations

import asyncio

from domain.dashboard import WatchlistMatrixRow
from domain.entities import WatchlistItem
from domain.entities import InvestmentMemoryRecord
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class WatchlistMatrixService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def _quote_row(
        self,
        item: WatchlistItem,
        memory: InvestmentMemoryRecord | None,
    ) -> WatchlistMatrixRow:
        try:
            quote, hist = await asyncio.gather(
                self._market.get_quote(item.ticker),
                self._market.get_history(item.ticker, period="5d", interval="1d"),
            )
            price = float(quote.get("current_price") or 0) or None
            change_pct = None
            if not hist.empty and len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                last = float(hist["Close"].iloc[-1])
                if prev:
                    change_pct = round((last - prev) / prev * 100, 2)

            scores = memory.scores if memory else {}
            return WatchlistMatrixRow(
                ticker=item.ticker.upper(),
                company_name=quote.get("company_name") or item.company_name,
                price=round(price, 2) if price else None,
                change_pct=change_pct,
                recommendation=memory.recommendation if memory else None,
                confidence=memory.confidence if memory else None,
                news_score=scores.get("news_agent"),
                technical_score=scores.get("technical_agent"),
                sentiment_score=scores.get("sentiment_agent"),
                analyzed_at=memory.created_at if memory else None,
            )
        except Exception as exc:
            logger.warning("matrix.row.failed", ticker=item.ticker, error=str(exc))
            return WatchlistMatrixRow(ticker=item.ticker.upper(), company_name=item.company_name)

    async def build(
        self,
        watchlist: list[WatchlistItem],
        memory_by_ticker: dict[str, InvestmentMemoryRecord],
    ) -> list[WatchlistMatrixRow]:
        if not watchlist:
            return []
        tasks = [
            self._quote_row(item, memory_by_ticker.get(item.ticker.upper()))
            for item in watchlist
        ]
        rows = await asyncio.gather(*tasks)
        return sorted(rows, key=lambda r: r.ticker)
