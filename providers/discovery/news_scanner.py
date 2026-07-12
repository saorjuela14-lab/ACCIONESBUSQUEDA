"""Thematic news scanning for company discovery."""

import asyncio
from datetime import datetime

from duckduckgo_search import DDGS

from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_DEFAULT_THEMES = (
    "breakout stock earnings beat",
    "biotech FDA approval stock",
    "AI semiconductor stock surge",
    "small cap growth stock analyst upgrade",
)


class NewsDiscoveryScanner:
    name = "news_discovery"

    @sync_retry
    def _search_news(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.news(query, max_results=max_results):
                results.append({
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "url": item.get("url", ""),
                    "source": item.get("source", "news"),
                    "date": item.get("date"),
                })
        return results

    async def scan(self, themes: list[str] | None = None, max_per_theme: int = 5) -> list[tuple[str, DiscoveryMention]]:
        queries = themes or list(_DEFAULT_THEMES)
        results: list[tuple[str, DiscoveryMention]] = []
        seen_urls: set[str] = set()

        for query in queries[:6]:
            try:
                hits = await asyncio.to_thread(self._search_news, query, max_per_theme)
                for hit in hits:
                    url = hit.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title = hit.get("title", "")
                    body = hit.get("body", "") or ""
                    text = f"{title} {body}".strip()
                    if not text:
                        continue

                    published = None
                    raw_date = hit.get("date")
                    if raw_date:
                        try:
                            published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                        except ValueError:
                            published = None

                    tickers = extract_tickers(text)
                    mention = DiscoveryMention(
                        source="news",
                        text=text[:500],
                        url=url,
                        sentiment=None,
                        author=hit.get("source"),
                        published_at=published,
                    )
                    if tickers:
                        for ticker in tickers[:3]:
                            results.append((ticker, mention))
                    else:
                        results.append(("", mention))
            except Exception as exc:
                logger.warning("discovery.news.search_failed", query=query, error=str(exc))

        return results
