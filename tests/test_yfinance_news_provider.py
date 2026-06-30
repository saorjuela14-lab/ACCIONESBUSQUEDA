"""YFinance news provider tests."""

from unittest.mock import MagicMock, patch

from providers.news.yfinance_news_provider import YFinanceNewsProvider


@patch("providers.news.yfinance_news_provider.yf.Ticker")
async def test_yfinance_news_parses_nested_content(mock_ticker):
    mock_ticker.return_value.news = [
        {
            "content": {
                "title": "FDA approves AbbVie Skyrizi for pediatric psoriasis",
                "summary": "AbbVie said the FDA approved Skyrizi for patients aged six years and older.",
                "pubDate": "2026-06-29T20:21:47Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://example.com/skyrizi"},
            }
        }
    ]

    provider = YFinanceNewsProvider()
    items = await provider.get_company_news("ABBV")

    assert len(items) == 1
    assert "FDA" in items[0].title
    assert items[0].snippet
    assert items[0].published_at is not None
    assert items[0].source == "Reuters"
