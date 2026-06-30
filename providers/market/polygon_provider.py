"""Polygon.io / Massive market data provider."""

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from config.settings import get_settings
from providers.interfaces import MarketDataProvider
from providers.market.intervals import INTERVAL_MAP, normalize_interval, period_to_date_range
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)


class PolygonProvider(MarketDataProvider):
    """Massive (formerly Polygon.io) aggregates API for quotes and OHLCV bars."""

    name = "polygon"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.polygon_api_key
        self._base_url = (base_url or settings.polygon_api_base_url).rstrip("/")
        if not self._api_key:
            raise ValueError("POLYGON_API_KEY is required")

    @async_retry
    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        query = params or {}

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try Bearer auth (Massive) first, then apiKey query param (legacy Polygon)
            for auth_mode in ("bearer", "query"):
                try:
                    if auth_mode == "bearer":
                        response = await client.get(
                            f"{self._base_url}{path}",
                            params=query,
                            headers=headers,
                        )
                    else:
                        response = await client.get(
                            f"{self._base_url}{path}",
                            params={**query, "apiKey": self._api_key},
                        )
                    response.raise_for_status()
                    data = response.json()
                    if data.get("status") == "ERROR":
                        raise ValueError(data.get("error", "Polygon/Massive API error"))
                    return data
                except httpx.HTTPStatusError as exc:
                    if auth_mode == "bearer" and exc.response.status_code == 401:
                        continue
                    raise

        raise ValueError("Polygon/Massive authentication failed")

    def _bars_to_dataframe(self, results: list[dict[str, Any]]) -> pd.DataFrame:
        if not results:
            return pd.DataFrame()
        rows = []
        for bar in results:
            ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc)
            rows.append(
                {
                    "Open": bar.get("o"),
                    "High": bar.get("h"),
                    "Low": bar.get("l"),
                    "Close": bar.get("c"),
                    "Volume": bar.get("v", 0),
                    "Datetime": ts,
                }
            )
        df = pd.DataFrame(rows)
        df = df.set_index("Datetime")
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        data = await self._request(f"/v2/aggs/ticker/{ticker}/prev")
        results = data.get("results", [])
        if not results:
            raise ValueError(f"No quote data for {ticker}")

        bar = results[0] if isinstance(results, list) else results
        price = bar.get("c")

        details: dict[str, Any] = {}
        try:
            ref = await self._request(f"/v3/reference/tickers/{ticker}")
            details = ref.get("results", {}) or {}
        except Exception as exc:
            logger.warning("polygon.ticker_details.failed", ticker=ticker, error=str(exc))

        return {
            "ticker": ticker,
            "company_name": details.get("name", ticker),
            "sector": None,
            "industry": None,
            "country": details.get("locale", "US"),
            "currency": details.get("currency_name", "USD"),
            "current_price": price,
            "market_cap": details.get("market_cap"),
            "info": details,
            "source": self.name,
        }

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        ticker = ticker.upper()
        interval = normalize_interval(interval)
        mapping = INTERVAL_MAP.get(interval, INTERVAL_MAP["1d"])
        polygon_map = mapping["polygon"]
        assert isinstance(polygon_map, tuple)
        multiplier, timespan = polygon_map

        from_date, to_date = period_to_date_range(period)
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        data = await self._request(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        results = data.get("results", [])
        df = self._bars_to_dataframe(results)
        if not df.empty:
            df.attrs["source"] = self.name
        return df

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        raise NotImplementedError("Polygon financials not implemented; use fallback provider")

    async def get_peers(self, ticker: str) -> list[str]:
        raise NotImplementedError("Polygon peers not implemented; use fallback provider")
