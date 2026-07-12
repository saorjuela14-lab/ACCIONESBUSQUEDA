"""Tests for X discovery provider (DuckDuckGo only)."""

from unittest.mock import patch

import pytest

from providers.discovery.x_search_provider import XSearchScanner


@pytest.mark.asyncio
async def test_x_scan_extracts_tickers_from_ddg():
    scanner = XSearchScanner()
    fake_hits = [{
        "title": "Breakout watch",
        "body": "$AAPL and NVDA looking strong on x.com",
        "url": "https://x.com/trader/status/1",
    }]

    with patch.object(scanner, "_search", return_value=fake_hits):
        results = await scanner.scan(extra_queries=["tech"], max_per_query=3)

    tickers = {t for t, _ in results if t}
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    assert all(m.source == "x" for _, m in results)


@pytest.mark.asyncio
async def test_x_scan_skips_duplicate_urls():
    scanner = XSearchScanner()
    fake_hits = [
        {"title": "Post 1", "body": "$TSLA", "url": "https://x.com/a/status/1"},
        {"title": "Post 1 dup", "body": "$TSLA again", "url": "https://x.com/a/status/1"},
    ]

    with patch.object(scanner, "_search", return_value=fake_hits):
        results = await scanner.scan(max_per_query=2)

    urls = [m.url for _, m in results]
    assert urls.count("https://x.com/a/status/1") == 1


@pytest.mark.asyncio
async def test_x_scan_adds_theme_queries():
    scanner = XSearchScanner()
    seen_queries: list[str] = []

    def capture(query: str, max_results: int):
        seen_queries.append(query)
        return []

    with patch.object(scanner, "_search", side_effect=capture):
        await scanner.scan(extra_queries=["biotech"], max_per_query=2)

    assert any("biotech" in q for q in seen_queries)
