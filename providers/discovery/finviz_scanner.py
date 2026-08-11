"""Discovery from Finviz market news (tickers mentioned in headlines)."""

from __future__ import annotations

from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from providers.finviz.client import FinvizClient, get_finviz_client
from utils.logging import get_logger

logger = get_logger(__name__)

# Common false positives on market wire headlines
_BLOCK = frozenset(
    {
        "CEO", "CFO", "CTO", "USA", "US", "UK", "EU", "AI", "IPO", "ETF", "GDP",
        "FED", "SEC", "FDA", "API", "CEO", "USD", "NYSE", "IMO", "CEO", "PDF",
        "CEO", "Q1", "Q2", "Q3", "Q4", "YOY", "MOM", "ATH", "AM", "PM",
    }
)


class FinvizDiscoveryScanner:
    name = "finviz"

    def __init__(self, client: FinvizClient | None = None) -> None:
        self._client = client or get_finviz_client()

    async def scan(self, max_headlines: int = 40) -> list[tuple[str, DiscoveryMention]]:
        try:
            rows = await self._client.get_market_news(max_results=max_headlines)
        except Exception as exc:
            logger.warning("discovery.finviz.failed", error=str(exc))
            return []

        results: list[tuple[str, DiscoveryMention]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            # Prefer cash/bare extractors with blocklist — ignore loose 1-letter tokens
            tickers = [t for t in extract_tickers(title) if t not in _BLOCK]
            mention = DiscoveryMention(
                source="finviz",
                text=title[:500],
                url=row.get("url"),
                sentiment=None,
                author=str(row.get("source") or "Finviz"),
                published_at=row.get("published_at"),
            )
            if not tickers:
                results.append(("", mention))
                continue
            for ticker in tickers[:3]:
                key = (ticker, title[:80])
                if key in seen:
                    continue
                seen.add(key)
                results.append((ticker, mention))
        logger.info("discovery.finviz.done", mentions=len(results), headlines=len(rows))
        return results
