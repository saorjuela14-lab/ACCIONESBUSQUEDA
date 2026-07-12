"""StockTwits trending symbols and stream mentions for discovery."""

import asyncio
from datetime import datetime

import httpx

from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)

STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"


class StockTwitsTrendingScanner:
    name = "stocktwits_trending"

    @async_retry
    async def _fetch_trending(self) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{STOCKTWITS_BASE}/trending/symbols.json")
            response.raise_for_status()
            return response.json()

    @async_retry
    async def _fetch_stream(self, ticker: str) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{STOCKTWITS_BASE}/streams/symbol/{ticker.upper()}.json")
            response.raise_for_status()
            return response.json()

    def _parse_message(self, msg: dict, default_ticker: str | None = None) -> tuple[str | None, DiscoveryMention]:
        symbol = default_ticker
        sym_obj = msg.get("symbol") or {}
        if sym_obj.get("symbol"):
            symbol = sym_obj["symbol"].upper()

        body = msg.get("body", "")
        if not symbol:
            extracted = extract_tickers(body)
            symbol = extracted[0] if extracted else None

        sentiment = None
        basic = ((msg.get("entities") or {}).get("sentiment") or {}).get("basic", "")
        if basic:
            sentiment = basic.lower()

        published = None
        created = msg.get("created_at")
        if created:
            try:
                published = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                published = None

        mention = DiscoveryMention(
            source="stocktwits",
            text=body[:500],
            url=f"https://stocktwits.com/symbol/{symbol}" if symbol else "https://stocktwits.com",
            sentiment=sentiment,
            author=(msg.get("user") or {}).get("username"),
            published_at=published,
        )
        return symbol, mention

    async def scan(self, max_symbols: int = 30) -> list[tuple[str, DiscoveryMention]]:
        """Return (ticker, mention) pairs from trending symbols and their streams."""
        results: list[tuple[str, DiscoveryMention]] = []

        try:
            data = await self._fetch_trending()
            symbols = data.get("symbols") or []
            for sym in symbols[:max_symbols]:
                ticker = (sym.get("symbol") or "").upper()
                if not ticker:
                    continue
                title = sym.get("title") or ticker
                results.append((
                    ticker,
                    DiscoveryMention(
                        source="stocktwits",
                        text=f"Trending: {title}",
                        url=f"https://stocktwits.com/symbol/{ticker}",
                        sentiment=None,
                    ),
                ))
        except Exception as exc:
            logger.warning("discovery.stocktwits.trending_failed", error=str(exc))

        stream_tasks = []
        tickers_to_fetch = list({t for t, _ in results})[:10]
        for ticker in tickers_to_fetch:
            stream_tasks.append(self._scan_stream(ticker))

        if stream_tasks:
            stream_results = await asyncio.gather(*stream_tasks, return_exceptions=True)
            for batch in stream_results:
                if isinstance(batch, list):
                    results.extend(batch)

        return results

    async def _scan_stream(self, ticker: str, max_messages: int = 8) -> list[tuple[str, DiscoveryMention]]:
        out: list[tuple[str, DiscoveryMention]] = []
        try:
            data = await self._fetch_stream(ticker)
            for msg in (data.get("messages") or [])[:max_messages]:
                sym, mention = self._parse_message(msg, default_ticker=ticker)
                if sym:
                    out.append((sym.upper(), mention))
        except Exception as exc:
            logger.warning("discovery.stocktwits.stream_failed", ticker=ticker, error=str(exc))
        return out
