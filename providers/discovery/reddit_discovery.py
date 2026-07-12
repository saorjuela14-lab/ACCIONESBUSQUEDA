"""Reddit discovery for trending tickers — extends sentiment search pattern."""

import asyncio

from duckduckgo_search import DDGS

from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_REDDIT_QUERIES = (
    "site:reddit.com/r/wallstreetbets stock DD OR YOLO",
    "site:reddit.com/r/stocks breakout OR undervalued",
    "site:reddit.com/r/investing emerging OR growth stock",
    "site:reddit.com/r/StockMarket momentum OR catalyst",
)


class RedditDiscoveryScanner:
    name = "reddit_discovery"

    @sync_retry
    def _search(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = item.get("href", "")
                if "reddit.com" not in url:
                    continue
                results.append({
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "url": url,
                })
        return results

    async def scan(self, extra_queries: list[str] | None = None, max_per_query: int = 6) -> list[tuple[str, DiscoveryMention]]:
        queries = list(_REDDIT_QUERIES)
        if extra_queries:
            for theme in extra_queries[:3]:
                queries.append(f"site:reddit.com {theme} stock")

        results: list[tuple[str, DiscoveryMention]] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                hits = await asyncio.to_thread(self._search, query, max_per_query)
                for hit in hits:
                    url = hit.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    text = f"{hit.get('title', '')} {hit.get('body', '')}".strip()
                    if not text:
                        continue

                    tickers = extract_tickers(text)
                    mention = DiscoveryMention(
                        source="reddit",
                        text=text[:500],
                        url=url,
                        sentiment=None,
                        author="reddit",
                    )
                    for ticker in tickers[:4]:
                        results.append((ticker, mention))
            except Exception as exc:
                logger.warning("discovery.reddit.search_failed", query=query, error=str(exc))

        return results
