"""YFinance market data provider."""

import asyncio
from typing import Any

import pandas as pd
import yfinance as yf

from providers.interfaces import MarketDataProvider
from utils.retry import sync_retry


class YFinanceProvider(MarketDataProvider):
    @sync_retry
    def _fetch_quote(self, ticker: str) -> dict[str, Any]:
        stock = yf.Ticker(ticker.upper())
        info = stock.info or {}
        hist = stock.history(period="5d")
        current_price = float(hist["Close"].iloc[-1]) if not hist.empty else info.get("currentPrice")
        return {
            "ticker": ticker.upper(),
            "company_name": info.get("longName", ticker.upper()),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "currency": info.get("currency", "USD"),
            "current_price": current_price,
            "market_cap": info.get("marketCap"),
            "info": info,
        }

    @sync_retry
    def _fetch_history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        return yf.Ticker(ticker.upper()).history(period=period, interval=interval)

    @sync_retry
    def _fetch_financials(self, ticker: str) -> dict[str, Any]:
        stock = yf.Ticker(ticker.upper())
        return {
            "info": stock.info or {},
            "income_stmt": stock.income_stmt.to_dict() if stock.income_stmt is not None else {},
            "balance_sheet": stock.balance_sheet.to_dict() if stock.balance_sheet is not None else {},
            "cashflow": stock.cashflow.to_dict() if stock.cashflow is not None else {},
            "earnings": stock.earnings.to_dict() if hasattr(stock, "earnings") and stock.earnings is not None else {},
            "recommendations": stock.recommendations.to_dict() if stock.recommendations is not None else {},
        }

    @sync_retry
    def _fetch_peers(self, ticker: str) -> list[str]:
        info = yf.Ticker(ticker.upper()).info or {}
        return info.get("recommendedSymbols", []) or []

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_quote, ticker)

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        return await asyncio.to_thread(self._fetch_history, ticker, period, interval)

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_financials, ticker)

    async def get_peers(self, ticker: str) -> list[str]:
        peers = await asyncio.to_thread(self._fetch_peers, ticker)
        if isinstance(peers, list) and peers and isinstance(peers[0], dict):
            return [p.get("symbol", "") for p in peers if p.get("symbol")]
        return peers if isinstance(peers, list) else []
