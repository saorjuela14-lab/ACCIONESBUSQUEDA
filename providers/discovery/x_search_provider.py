"""X (Twitter) discovery — official API v2 when configured, DuckDuckGo fallback."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime

import httpx
from duckduckgo_search import DDGS

from config.settings import get_settings
from domain.discovery import DiscoveryMention
from providers.discovery.ticker_extractor import extract_tickers
from utils.logging import get_logger
from utils.retry import async_retry, sync_retry

logger = get_logger(__name__)

_X_API_BASE = "https://api.twitter.com/2"
_X_DDG_QUERIES = (
    "site:x.com stock bullish OR breakout",
    "site:twitter.com $ stock buy OR long",
    "site:x.com biotech OR semiconductor stock",
    "site:twitter.com small cap stock momentum",
)
_X_API_QUERIES = (
    '(stock OR stocks) (bullish OR breakout OR momentum) -is:retweet lang:en',
    '(biotech OR semiconductor OR "artificial intelligence") (stock OR $) -is:retweet lang:en',
    '("small cap" OR "growth stock") (buy OR long OR breakout) -is:retweet lang:en',
)


class XSearchScanner:
    name = "x_search"

    def __init__(self) -> None:
        self._bearer_cache: str | None = None

    def _api_enabled(self) -> bool:
        settings = get_settings()
        return bool(settings.x_bearer_token or (settings.x_api_key and settings.x_api_secret))

    async def _resolve_bearer(self) -> str | None:
        if self._bearer_cache:
            return self._bearer_cache

        settings = get_settings()
        if settings.x_bearer_token:
            self._bearer_cache = settings.x_bearer_token
            return self._bearer_cache

        if not settings.x_api_key or not settings.x_api_secret:
            return None

        credentials = base64.b64encode(
            f"{settings.x_api_key}:{settings.x_api_secret}".encode()
        ).decode()

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.twitter.com/oauth2/token",
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"grant_type": "client_credentials"},
                )
                response.raise_for_status()
                token = response.json().get("access_token")
                if token:
                    self._bearer_cache = token
                    logger.info("discovery.x.bearer_obtained")
                return self._bearer_cache
        except Exception as exc:
            logger.warning("discovery.x.bearer_failed", error=str(exc))
            return None

    @async_retry
    async def _search_api(self, query: str, max_results: int) -> list[dict]:
        bearer = await self._resolve_bearer()
        if not bearer:
            return []

        params = {
            "query": query,
            "max_results": min(max(max_results, 10), 100),
            "tweet.fields": "created_at,author_id,public_metrics",
            "expansions": "author_id",
            "user.fields": "username",
        }
        headers = {"Authorization": f"Bearer {bearer}"}

        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(
                f"{_X_API_BASE}/tweets/search/recent",
                params=params,
                headers=headers,
            )
            if response.status_code == 429:
                logger.warning("discovery.x.rate_limited", query=query)
                return []
            if response.status_code == 402:
                logger.warning(
                    "discovery.x.credits_depleted",
                    query=query,
                    detail="Créditos de X API agotados — usa plan de pago o fallback DDG",
                )
                return []
            response.raise_for_status()
            payload = response.json()

        users = {u["id"]: u for u in (payload.get("includes") or {}).get("users", [])}
        hits: list[dict] = []
        for tweet in payload.get("data") or []:
            author = users.get(tweet.get("author_id", ""), {})
            username = author.get("username", "x")
            tweet_id = tweet.get("id", "")
            hits.append({
                "text": tweet.get("text", ""),
                "url": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else None,
                "author": f"@{username}",
                "published_at": tweet.get("created_at"),
            })
        return hits

    @sync_retry
    def _search_ddg(self, query: str, max_results: int) -> list[dict]:
        results: list[dict] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = item.get("href", "")
                if "x.com" not in url and "twitter.com" not in url:
                    continue
                results.append({
                    "text": f"{item.get('title', '')} {item.get('body', '')}".strip(),
                    "url": url,
                    "author": "x.com",
                    "published_at": None,
                })
        return results

    def _parse_published_at(self, raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _hits_to_mentions(self, hits: list[dict]) -> list[tuple[str, DiscoveryMention]]:
        results: list[tuple[str, DiscoveryMention]] = []
        seen_urls: set[str] = set()

        for hit in hits:
            url = hit.get("url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            text = (hit.get("text") or "").strip()
            if not text:
                continue

            tickers = extract_tickers(text)
            mention = DiscoveryMention(
                source="x",
                text=text[:500],
                url=url or None,
                sentiment=None,
                author=hit.get("author"),
                published_at=self._parse_published_at(hit.get("published_at")),
            )
            if tickers:
                for ticker in tickers[:3]:
                    results.append((ticker, mention))
            else:
                results.append(("", mention))
        return results

    async def _scan_api(self, extra_queries: list[str] | None, max_per_query: int) -> list[tuple[str, DiscoveryMention]]:
        queries = list(_X_API_QUERIES)
        if extra_queries:
            for theme in extra_queries[:3]:
                safe = theme.replace('"', "").strip()
                if safe:
                    queries.append(f'({safe}) (stock OR $) -is:retweet lang:en')

        results: list[tuple[str, DiscoveryMention]] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                hits = await self._search_api(query, max_per_query)
                for ticker, mention in self._hits_to_mentions(hits):
                    key = mention.url or mention.text[:80]
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    results.append((ticker, mention))
            except Exception as exc:
                logger.warning("discovery.x.api_search_failed", query=query, error=str(exc))

        return results

    async def _scan_ddg(self, extra_queries: list[str] | None, max_per_query: int) -> list[tuple[str, DiscoveryMention]]:
        queries = list(_X_DDG_QUERIES)
        if extra_queries:
            for theme in extra_queries[:3]:
                queries.append(f"site:x.com {theme} stock $")

        results: list[tuple[str, DiscoveryMention]] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                hits = await asyncio.to_thread(self._search_ddg, query, max_per_query)
                for ticker, mention in self._hits_to_mentions(hits):
                    key = mention.url or mention.text[:80]
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    results.append((ticker, mention))
            except Exception as exc:
                logger.warning("discovery.x.ddg_search_failed", query=query, error=str(exc))

        return results

    async def scan(self, extra_queries: list[str] | None = None, max_per_query: int = 6) -> list[tuple[str, DiscoveryMention]]:
        if self._api_enabled():
            api_results = await self._scan_api(extra_queries, max_per_query)
            if api_results:
                logger.info("discovery.x.api_hits", count=len(api_results))
                return api_results
            logger.warning("discovery.x.api_empty_fallback_ddg")

        return await self._scan_ddg(extra_queries, max_per_query)
