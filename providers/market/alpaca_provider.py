"""Alpaca Market Data API provider (stocks quotes + bars).

Base URL: https://data.alpaca.markets
Auth: APCA-API-KEY-ID / APCA-API-SECRET-KEY
Docs: https://docs.alpaca.markets/docs/getting-started-with-alpaca-market-data
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

from config.settings import get_settings
from providers.interfaces import MarketDataProvider
from providers.market.intervals import INTERVAL_MAP, normalize_interval, period_to_date_range
from utils.logging import get_logger

logger = get_logger(__name__)

DATA_BASE_URL = "https://data.alpaca.markets"


class AlpacaMarketDataProvider(MarketDataProvider):
    """Historical bars + latest trade via Alpaca Market Data REST API."""

    name = "alpaca"

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        feed: str | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (api_key if api_key is not None else settings.alpaca_api_key).strip()
        self._secret_key = (secret_key if secret_key is not None else settings.alpaca_secret_key).strip()
        self._base_url = (base_url or settings.alpaca_data_base_url or DATA_BASE_URL).rstrip("/")
        self._feed = (feed or settings.alpaca_data_feed or "iex").strip().lower()
        self.last_request_id: str | None = None
        if not self._api_key or not self._secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for market data")

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Accept": "application/json",
        }

    def _capture_request_id(self, response: httpx.Response) -> None:
        rid = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        if rid:
            self.last_request_id = rid

    def _raise_for_status(self, response: httpx.Response) -> None:
        self._capture_request_id(response)
        if response.is_success:
            return
        detail = ""
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or str(body)
        except Exception:
            detail = response.text[:300]
        rid = self.last_request_id or "n/a"
        raise httpx.HTTPStatusError(
            f"Alpaca data {response.status_code}: {detail} (X-Request-ID: {rid})",
            request=response.request,
            response=response,
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers(), params=params or {})
            self._raise_for_status(response)
            data = response.json()
            if isinstance(data, dict):
                data["_request_id"] = self.last_request_id
            return data

    def _timeframe(self, interval: str) -> str:
        key = normalize_interval(interval)
        mapping = INTERVAL_MAP.get(key, {})
        tf = mapping.get("alpaca")
        if isinstance(tf, str):
            return tf
        # sensible defaults
        defaults = {
            "1m": "1Min",
            "2m": "1Min",
            "5m": "5Min",
            "15m": "15Min",
            "30m": "30Min",
            "60m": "1Hour",
            "1h": "1Hour",
            "4h": "1Hour",
            "1d": "1Day",
            "1wk": "1Week",
            "1mo": "1Month",
        }
        return defaults.get(key, "1Day")

    def _bars_to_dataframe(self, bars: list[dict[str, Any]]) -> pd.DataFrame:
        if not bars:
            return pd.DataFrame()
        rows = []
        for bar in bars:
            ts_raw = bar.get("t")
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(timezone.utc)
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
        symbol = ticker.upper()
        data = await self._get(
            f"/v2/stocks/{symbol}/trades/latest",
            params={"feed": self._feed},
        )
        trade = data.get("trade") or {}
        price = trade.get("p")
        return {
            "ticker": symbol,
            "company_name": symbol,
            "sector": None,
            "industry": None,
            "country": "US",
            "currency": data.get("currency") or "USD",
            "current_price": float(price) if price is not None else None,
            "market_cap": None,
            "source": "alpaca",
            "info": {
                "alpaca_trade": trade,
                "feed": self._feed,
                "request_id": data.get("_request_id") or self.last_request_id,
            },
        }

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        symbol = ticker.upper()
        start, end = period_to_date_range(period)
        timeframe = self._timeframe(interval)
        all_bars: list[dict[str, Any]] = []
        page_token: str | None = None

        for _ in range(20):  # pagination safety
            params: dict[str, Any] = {
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "adjustment": "all",
                "feed": self._feed,
                "limit": 10000,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            data = await self._get(f"/v2/stocks/{symbol}/bars", params=params)
            bars = data.get("bars") or []
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break

        logger.info(
            "alpaca.market.history",
            ticker=symbol,
            bars=len(all_bars),
            timeframe=timeframe,
            request_id=self.last_request_id,
        )
        return self._bars_to_dataframe(all_bars)

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        raise NotImplementedError("Alpaca Market Data does not provide fundamentals")

    async def get_peers(self, ticker: str) -> list[str]:
        raise NotImplementedError("Alpaca Market Data does not provide peers")
