"""Finviz HTML parsing + provider wiring (offline fixtures)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from providers.finviz.client import FinvizClient, _parse_finviz_time, _parse_number
from providers.news.finviz_news_provider import FinvizNewsProvider
from providers.discovery.finviz_scanner import FinvizDiscoveryScanner


_QUOTE_HTML = """
<html><body>
  <h2 class="quote-header_ticker-wrapper_company">Apple Inc</h2>
  <a class="tab-link" href="screener.ashx?f=sec_technology">Technology</a>
  <a class="tab-link" href="screener.ashx?f=ind_consumerelectronics">Consumer Electronics</a>
  <table>
    <tr>
      <td><div class="snapshot-td-label">Price</div></td>
      <td class="snapshot-td2"><div class="snapshot-td-content"><b>190.50</b></div></td>
      <td><div class="snapshot-td-label">P/E</div></td>
      <td class="snapshot-td2"><div class="snapshot-td-content">28.1</div></td>
    </tr>
    <tr>
      <td><div class="snapshot-td-label">Short Float</div></td>
      <td class="snapshot-td2">1.25%</td>
      <td><div class="snapshot-td-label">Rel Volume</div></td>
      <td class="snapshot-td2">1.80</td>
    </tr>
    <tr>
      <td><div class="snapshot-td-label">Market Cap</div></td>
      <td class="snapshot-td2">2.95T</td>
      <td><div class="snapshot-td-label">Perf Week</div></td>
      <td class="snapshot-td2">2.40%</td>
    </tr>
  </table>
  <table class="news-table">
    <tr>
      <td>Aug-10-26 09:33AM</td>
      <td><a href="https://example.com/aapl">Apple unveils new product</a>
          <span class="news-link-right">(Reuters)</span></td>
    </tr>
    <tr>
      <td>10:15AM</td>
      <td><a href="https://example.com/msft">MSFT and NVDA rally on AI</a>
          <span class="news-link-right">(Bloomberg)</span></td>
    </tr>
  </table>
</body></html>
"""


def test_parse_number_suffixes():
    assert _parse_number("2.95T") == pytest.approx(2.95e12)
    assert _parse_number("4498.80B") == pytest.approx(4498.80e9)
    assert _parse_number("56.74M") == pytest.approx(56.74e6)
    assert _parse_number("1.25%") == pytest.approx(1.25)


def test_parse_finviz_time():
    dt = _parse_finviz_time("Aug-10-26 09:33PM")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 8
    assert dt.day == 10
    assert dt.hour == 21


def test_parse_quote_snapshot():
    client = FinvizClient()
    q = client.parse_quote_snapshot(_QUOTE_HTML, "AAPL")
    assert q["ticker"] == "AAPL"
    assert q["company_name"] == "Apple Inc"
    assert q["current_price"] == pytest.approx(190.50)
    assert q["pe"] == pytest.approx(28.1)
    assert q["short_float_pct"] == pytest.approx(1.25)
    assert q["rel_volume"] == pytest.approx(1.80)
    assert q["sector"] == "Technology"
    assert q["industry"] == "Consumer Electronics"
    assert q["delayed"] is True


def test_parse_news_table():
    client = FinvizClient()
    news = client.parse_news_table(_QUOTE_HTML, max_results=10)
    assert len(news) >= 2
    assert news[0]["title"].startswith("Apple")
    assert news[0]["source"] == "Reuters"
    assert "NVDA" in (news[1].get("related_tickers") or []) or "MSFT" in (
        news[1].get("related_tickers") or []
    )


@pytest.mark.asyncio
async def test_finviz_news_provider():
    client = MagicMock()
    client.get_ticker_news = AsyncMock(
        return_value=[
            {
                "title": "Apple upgrades guidance",
                "url": "https://example.com/1",
                "source": "Finviz",
                "published_at": datetime(2026, 8, 10),
                "related_tickers": ["AAPL"],
            }
        ]
    )
    items = await FinvizNewsProvider(client).get_company_news("AAPL", max_results=5)
    assert len(items) == 1
    assert items[0].title.startswith("Apple")


@pytest.mark.asyncio
async def test_finviz_discovery_scanner():
    client = MagicMock()
    client.get_market_news = AsyncMock(
        return_value=[
            {
                "title": "NVDA hits record on AI demand",
                "url": "https://example.com/n",
                "source": "MarketWatch",
                "published_at": None,
                "related_tickers": ["NVDA"],
            }
        ]
    )
    hits = await FinvizDiscoveryScanner(client).scan()
    tickers = [t for t, _ in hits if t]
    assert "NVDA" in tickers
