"""Tests for X discovery provider (API + DuckDuckGo fallback)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.discovery import DiscoveryMention
from providers.discovery.x_search_provider import XSearchScanner


@pytest.mark.asyncio
async def test_x_scan_uses_ddg_when_no_credentials():
    scanner = XSearchScanner()
    with patch.object(scanner, "_api_enabled", return_value=False), \
         patch.object(scanner, "_scan_ddg", new_callable=AsyncMock) as ddg:
        ddg.return_value = [("AAPL", DiscoveryMention(source="x", text="$AAPL breakout"))]
        results = await scanner.scan(extra_queries=["tech"])
        assert len(results) == 1
        assert results[0][0] == "AAPL"
        ddg.assert_called_once()


@pytest.mark.asyncio
async def test_x_scan_api_when_configured():
    scanner = XSearchScanner()
    api_hits = [("NVDA", DiscoveryMention(source="x", text="$NVDA AI momentum", url="https://x.com/u/status/1"))]
    with patch.object(scanner, "_api_enabled", return_value=True), \
         patch.object(scanner, "_scan_api", new_callable=AsyncMock) as api, \
         patch.object(scanner, "_scan_ddg", new_callable=AsyncMock) as ddg:
        api.return_value = api_hits
        results = await scanner.scan()
        assert results == api_hits
        ddg.assert_not_called()


@pytest.mark.asyncio
async def test_x_scan_falls_back_to_ddg_when_api_empty():
    scanner = XSearchScanner()
    ddg_hits = [("TSLA", DiscoveryMention(source="x", text="TSLA long"))]
    with patch.object(scanner, "_api_enabled", return_value=True), \
         patch.object(scanner, "_scan_api", new_callable=AsyncMock, return_value=[]), \
         patch.object(scanner, "_scan_ddg", new_callable=AsyncMock, return_value=ddg_hits) as ddg:
        results = await scanner.scan()
        assert results == ddg_hits
        ddg.assert_called_once()


@pytest.mark.asyncio
async def test_x_bearer_from_key_secret():
    scanner = XSearchScanner()
    mock_settings = MagicMock(
        x_bearer_token="",
        x_api_key="key123",
        x_api_secret="secret456",
    )
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"access_token": "generated-bearer"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.discovery.x_search_provider.get_settings", return_value=mock_settings), \
         patch("providers.discovery.x_search_provider.httpx.AsyncClient", return_value=mock_client):
        token = await scanner._resolve_bearer()
        assert token == "generated-bearer"
        assert scanner._bearer_cache == "generated-bearer"


@pytest.mark.asyncio
async def test_x_search_api_parses_tweets():
    scanner = XSearchScanner()
    scanner._bearer_cache = "test-bearer"

    payload = {
        "data": [
            {"id": "99", "text": "$AAPL looking strong", "author_id": "1", "created_at": "2026-01-15T14:00:00Z"},
        ],
        "includes": {"users": [{"id": "1", "username": "trader1"}]},
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = payload

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("providers.discovery.x_search_provider.httpx.AsyncClient", return_value=mock_client):
        hits = await scanner._search_api("(stock) -is:retweet", 10)

    assert len(hits) == 1
    assert hits[0]["text"] == "$AAPL looking strong"
    assert hits[0]["url"] == "https://x.com/trader1/status/99"
    assert hits[0]["author"] == "@trader1"
