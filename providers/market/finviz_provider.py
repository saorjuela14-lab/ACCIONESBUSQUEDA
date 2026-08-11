"""Finviz market snapshot — delayed quotes + fundamentals (no OHLCV history)."""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from providers.finviz.client import FinvizClient, get_finviz_client
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger

logger = get_logger(__name__)


class FinvizMarketProvider(MarketDataProvider):
    name = "finviz"

    def __init__(self, client: FinvizClient | None = None) -> None:
        self._client = client or get_finviz_client()

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        try:
            return await self._client.get_quote(ticker.upper())
        except Exception as exc:
            logger.warning("finviz.quote.failed", ticker=ticker, error=str(exc))
            raise

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        # Finviz HTML does not expose reliable OHLCV bars for the desk chain.
        _ = (ticker, period, interval)
        return pd.DataFrame()

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        quote = await self.get_quote(ticker)
        snap = quote.get("snapshot") or {}
        info = {
            "symbol": ticker.upper(),
            "shortName": quote.get("company_name"),
            "longName": quote.get("company_name"),
            "sector": quote.get("sector"),
            "industry": quote.get("industry"),
            "country": quote.get("country"),
            "marketCap": quote.get("market_cap"),
            "trailingPE": quote.get("pe"),
            "forwardPE": quote.get("forward_pe"),
            "trailingEps": quote.get("eps_ttm"),
            "targetMeanPrice": quote.get("target_price"),
            "beta": quote.get("beta"),
            "shortPercentOfFloat": (
                (quote.get("short_float_pct") or 0) / 100.0
                if quote.get("short_float_pct") is not None
                else None
            ),
            "recommendationKey": quote.get("analyst_recom"),
            "finviz_snapshot": snap,
            "source": "finviz",
        }
        return {
            "info": info,
            "income_stmt": {},
            "balance_sheet": {},
            "cashflow": {},
            "source": "finviz",
        }

    async def get_peers(self, ticker: str) -> list[str]:
        _ = ticker
        return []
