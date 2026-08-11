"""Lightweight Finviz.com HTML client (quote snapshot + news).

Finviz quotes are delayed (~15–20 min). Used for research/enrichment, not live fills.
Screener endpoints may return 403; quote + news pages are the reliable surface.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import httpx
from bs4 import BeautifulSoup

from utils.logging import get_logger
from utils.retry import sync_retry

logger = get_logger(__name__)

_BASE = "https://finviz.com"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("%", "")
    if not s or s in ("-", "—"):
        return None
    mult = 1.0
    if s.endswith("T"):
        mult = 1e12
        s = s[:-1]
    elif s.endswith("B"):
        mult = 1e9
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1e6
        s = s[:-1]
    elif s.endswith("K"):
        mult = 1e3
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _parse_pct(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("%", "").replace(",", "")
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_finviz_time(raw: str, *, default_date: datetime | None = None) -> datetime | None:
    """Parse 'Aug-10-26 09:33PM' or bare '09:33PM'."""
    text = (raw or "").strip()
    if not text:
        return None
    now = default_date or datetime.now(timezone.utc)
    try:
        if re.match(r"^\d{1,2}:\d{2}\s*[AP]M$", text, re.I):
            t = datetime.strptime(text.upper().replace(" ", ""), "%I:%M%p")
            return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        # Aug-10-26 09:33PM
        m = re.match(
            r"^([A-Za-z]{3})-(\d{1,2})-(\d{2})\s+(\d{1,2}:\d{2}\s*[AP]M)$",
            text,
            re.I,
        )
        if m:
            mon, day, yy, tm = m.groups()
            year = 2000 + int(yy)
            t = datetime.strptime(tm.upper().replace(" ", ""), "%I:%M%p")
            dt = datetime.strptime(f"{mon}-{int(day):02d}-{year}", "%b-%d-%Y")
            return dt.replace(
                hour=t.hour, minute=t.minute, second=0, microsecond=0, tzinfo=timezone.utc
            )
    except ValueError:
        return None
    return None


class FinvizClient:
    name = "finviz"

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout
        self._headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{_BASE}/",
        }

    @sync_retry
    def _get_html(self, path: str) -> str:
        url = path if path.startswith("http") else f"{_BASE}{path}"
        with httpx.Client(timeout=self._timeout, follow_redirects=True, headers=self._headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise RuntimeError(f"finviz HTTP {resp.status_code} for {url}")
            return resp.text

    async def fetch_html(self, path: str) -> str:
        return await asyncio.to_thread(self._get_html, path)

    def parse_quote_snapshot(self, html: str, ticker: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        snapshot: dict[str, str] = {}
        for label in soup.select(".snapshot-td-label"):
            key = label.get_text(strip=True)
            td = label.find_parent("td")
            nxt = td.find_next_sibling("td") if td else None
            if key and nxt is not None:
                snapshot[key] = nxt.get_text(strip=True)

        company = None
        for sel in (".quote-header_ticker-wrapper_company", "h2.quote-header_ticker-wrapper_company", "h2"):
            el = soup.select_one(sel)
            if el:
                company = el.get_text(strip=True) or None
                if company:
                    break

        sector = industry = country = None
        for a in soup.select("a.tab-link, a.quote-header_category, a[href*='sec_'], a[href*='ind_'], a[href*='geo_']"):
            href = a.get("href") or ""
            text = a.get_text(strip=True)
            if "sec_" in href and not sector:
                sector = text
            elif "ind_" in href and not industry:
                industry = text
            elif "geo_" in href and not country:
                country = text

        price = _parse_number(snapshot.get("Price"))
        return {
            "ticker": ticker.upper(),
            "company_name": company or ticker.upper(),
            "sector": sector,
            "industry": industry,
            "country": country,
            "currency": "USD",
            "current_price": price,
            "market_cap": _parse_number(snapshot.get("Market Cap")),
            "pe": _parse_number(snapshot.get("P/E")),
            "forward_pe": _parse_number(snapshot.get("Forward P/E")),
            "eps_ttm": _parse_number(snapshot.get("EPS (ttm)")),
            "target_price": _parse_number(snapshot.get("Target Price")),
            "beta": _parse_number(snapshot.get("Beta")),
            "atr": _parse_number(snapshot.get("ATR")),
            "short_float_pct": _parse_pct(snapshot.get("Short Float")),
            "rel_volume": _parse_number(snapshot.get("Rel Volume")),
            "avg_volume": _parse_number(snapshot.get("Avg Volume")),
            "volume": _parse_number(snapshot.get("Volume")),
            "perf_week_pct": _parse_pct(snapshot.get("Perf Week")),
            "perf_month_pct": _parse_pct(snapshot.get("Perf Month")),
            "analyst_recom": snapshot.get("Analyst Recom"),
            "insider_own_pct": _parse_pct(snapshot.get("Insider Own")),
            "inst_own_pct": _parse_pct(snapshot.get("Inst Own")),
            "snapshot": snapshot,
            "source": "finviz",
            "delayed": True,
        }

    def parse_news_table(self, html: str, *, max_results: int = 30) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.news-table") or soup.select_one("#news-table")
        items: list[dict[str, Any]] = []
        if not table:
            # Market news page: collect external headline links
            for a in soup.select("a"):
                href = a.get("href") or ""
                title = a.get_text(strip=True)
                if not title or len(title) < 28:
                    continue
                if not href.startswith("http"):
                    continue
                if "finviz.com" in href:
                    continue
                items.append(
                    {
                        "title": title,
                        "url": href,
                        "source": "Finviz",
                        "published_at": None,
                        "related_tickers": _TICKER_RE.findall(title)[:3],
                    }
                )
                if len(items) >= max_results:
                    break
            return items

        last_date: datetime | None = None
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            a = tr.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title or title.lower() == "loading…":
                continue
            href = a.get("href") or ""
            if href.startswith("/"):
                href = f"{_BASE}{href}"
            time_raw = tds[0].get_text(strip=True) if tds else ""
            published = _parse_finviz_time(time_raw, default_date=last_date)
            if published and re.search(r"[A-Za-z]{3}-\d", time_raw):
                last_date = published
            src_el = tr.select_one(".news-link-right") or tr.find("span")
            source = (src_el.get_text(strip=True) if src_el else "Finviz").strip("() ") or "Finviz"
            items.append(
                {
                    "title": title,
                    "url": href,
                    "source": source,
                    "published_at": published,
                    "related_tickers": _TICKER_RE.findall(title)[:3],
                }
            )
            if len(items) >= max_results:
                break
        return items

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        html = await self.fetch_html(f"/quote.ashx?t={ticker.upper()}")
        return self.parse_quote_snapshot(html, ticker)

    async def get_ticker_news(self, ticker: str, max_results: int = 20) -> list[dict[str, Any]]:
        html = await self.fetch_html(f"/quote.ashx?t={ticker.upper()}")
        return self.parse_news_table(html, max_results=max_results)

    async def get_market_news(self, max_results: int = 40) -> list[dict[str, Any]]:
        html = await self.fetch_html("/news.ashx")
        return self.parse_news_table(html, max_results=max_results)


@lru_cache(maxsize=1)
def get_finviz_client() -> FinvizClient:
    return FinvizClient()
