"""Alpha Vantage market data provider."""

import asyncio
from typing import Any

import httpx
import pandas as pd

from config.settings import get_settings
from providers.interfaces import MarketDataProvider
from providers.market.intervals import INTERVAL_MAP, normalize_interval
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)

AV_BASE = "https://www.alphavantage.co/query"


class AlphaVantageProvider(MarketDataProvider):
    """Alpha Vantage API for quotes and intraday/daily time series."""

    name = "alpha_vantage"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.alpha_vantage_api_key
        if not self._api_key:
            raise ValueError("ALPHA_VANTAGE_API_KEY is required")

    @async_retry
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        params["apikey"] = self._api_key
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(AV_BASE, params=params)
            response.raise_for_status()
            data = response.json()

        if "Error Message" in data:
            raise ValueError(data["Error Message"])
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information", "Rate limit")
            raise ValueError(f"Alpha Vantage limit: {msg}")

        return data

    def _parse_intraday(self, data: dict[str, Any], interval_key: str) -> pd.DataFrame:
        series_key = next((k for k in data if "Time Series" in k), None)
        if not series_key:
            return pd.DataFrame()

        series = data[series_key]
        rows = []
        for ts, values in series.items():
            rows.append(
                {
                    "Datetime": pd.to_datetime(ts),
                    "Open": float(values.get("1. open", 0)),
                    "High": float(values.get("2. high", 0)),
                    "Low": float(values.get("3. low", 0)),
                    "Close": float(values.get("4. close", 0)),
                    "Volume": float(values.get("5. volume", 0)),
                }
            )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).set_index("Datetime").sort_index()
        df.attrs["source"] = self.name
        return df

    def _parse_daily(self, data: dict[str, Any], key_fragment: str) -> pd.DataFrame:
        series_key = next((k for k in data if key_fragment in k), None)
        if not series_key:
            return pd.DataFrame()

        series = data[series_key]
        rows = []
        for ts, values in series.items():
            rows.append(
                {
                    "Datetime": pd.to_datetime(ts),
                    "Open": float(values.get("1. open", 0)),
                    "High": float(values.get("2. high", 0)),
                    "Low": float(values.get("3. low", 0)),
                    "Close": float(values.get("4. close", 0)),
                    "Volume": float(values.get("6. volume", values.get("5. volume", 0))),
                }
            )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).set_index("Datetime").sort_index()
        df.attrs["source"] = self.name
        return df

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        data = await self._request({"function": "GLOBAL_QUOTE", "symbol": ticker.upper()})
        quote = data.get("Global Quote", {})
        if not quote:
            raise ValueError(f"No quote for {ticker}")

        price = float(quote.get("05. price", 0))
        return {
            "ticker": ticker.upper(),
            "company_name": ticker.upper(),
            "sector": None,
            "industry": None,
            "country": "US",
            "currency": "USD",
            "current_price": price,
            "market_cap": None,
            "info": quote,
            "source": self.name,
        }

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        ticker = ticker.upper()
        interval = normalize_interval(interval)
        mapping = INTERVAL_MAP.get(interval, INTERVAL_MAP["1d"])
        av_interval = str(mapping["alpha_vantage"])

        if av_interval in ("1min", "5min", "15min", "30min", "60min"):
            # Alpha Vantage intraday limited to ~30 days; use outputsize=full cautiously
            outputsize = "compact" if period in ("5d", "1mo") else "compact"
            data = await self._request(
                {
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": ticker,
                    "interval": av_interval,
                    "outputsize": outputsize,
                    "adjusted": "true",
                }
            )
            return self._parse_intraday(data, av_interval)

        if av_interval == "daily":
            data = await self._request(
                {
                    "function": "TIME_SERIES_DAILY",
                    "symbol": ticker,
                    "outputsize": "full" if period in ("1y", "5y", "10y", "max") else "compact",
                }
            )
            return self._parse_daily(data, "Daily")

        if av_interval == "weekly":
            data = await self._request({"function": "TIME_SERIES_WEEKLY_ADJUSTED", "symbol": ticker})
            return self._parse_daily(data, "Weekly")

        if av_interval == "monthly":
            data = await self._request({"function": "TIME_SERIES_MONTHLY_ADJUSTED", "symbol": ticker})
            return self._parse_daily(data, "Monthly")

        return pd.DataFrame()

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        overview, income = await asyncio.gather(
            self._request({"function": "OVERVIEW", "symbol": ticker.upper()}),
            self._request({"function": "INCOME_STATEMENT", "symbol": ticker.upper()}),
            return_exceptions=True,
        )
        info: dict[str, Any] = {}
        if isinstance(overview, dict) and "Symbol" in overview:
            info = overview

        income_data = {}
        if isinstance(income, dict):
            reports = income.get("annualReports", [])
            if reports:
                income_data = reports[0]

        return {
            "info": {
                "longName": info.get("Name"),
                "sector": info.get("Sector"),
                "industry": info.get("Industry"),
                "country": info.get("Country"),
                "trailingPE": _safe_float(info.get("PERatio")),
                "forwardPE": _safe_float(info.get("ForwardPE")),
                "priceToBook": _safe_float(info.get("PriceToBookRatio")),
                "marketCap": _safe_float(info.get("MarketCapitalization")),
                "dividendYield": _safe_float(info.get("DividendYield")),
                "eps": _safe_float(info.get("EPS")),
            },
            "income_stmt": income_data,
            "balance_sheet": {},
            "cashflow": {},
            "source": self.name,
        }

    async def get_peers(self, ticker: str) -> list[str]:
        return []


def _safe_float(value: Any) -> float | None:
    if value in (None, "None", "-", ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
