"""X (Twitter) discovery via DuckDuckGo site search — no API key required."""

import asyncio

from duckduckgo_search import DDGS

from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_X_QUERIES = (
    "site:x.com stock bullish OR breakout",
    "site:twitter.com $ stock buy OR long",
    "site:x.com biotech OR semiconductor stock",
    "site:twitter.com small cap stock momentum",
)


class XSearchScanner:
    name = "x_search"

    @sync_retry
    def _search(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = item.get("href", "")
                if "x.com" not in url and "twitter.com" not in url:
                    continue
                results.append({
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "url": url,
                })
        return results

    async def scan(self, extra_queries: list[str] | None = None, max_per_query: int = 6) -> list[tuple[str, DiscoveryMention]]:
        queries = list(_X_QUERIES)
        if extra_queries:
            for theme in extra_queries[:3]:
                queries.append(f"site:x.com {theme} stock $")

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
                        source="x",
                        text=text[:500],
                        url=url,
                        sentiment=None,
                        author="x.com",
                    )
                    if tickers:
                        for ticker in tickers[:3]:
                            results.append((ticker, mention))
                    else:
                        results.append(("", mention))
            except Exception as exc:
                logger.warning("discovery.x.search_failed", query=query, error=str(exc))

        return results
