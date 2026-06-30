"""Macro data provider using market proxies via yfinance."""

import asyncio
from typing import Any

import yfinance as yf

from providers.interfaces import MacroProvider
from utils.retry import sync_retry

_MACRO_TICKERS = {
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "GOLD": "GC=F",
    "OIL": "CL=F",
    "BTC": "BTC-USD",
}


class YFinanceMacroProvider(MacroProvider):
    @sync_retry
    def _fetch_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"indicators": {}, "references": []}
        for name, symbol in _MACRO_TICKERS.items():
            try:
                hist = yf.Ticker(symbol).history(period="5d")
                if hist.empty:
                    continue
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current
                change_pct = ((current - prev) / prev) * 100 if prev else 0.0
                snapshot["indicators"][name] = {
                    "symbol": symbol,
                    "value": round(current, 4),
                    "change_pct": round(change_pct, 2),
                }
                snapshot["references"].append({"source": "yfinance", "symbol": symbol, "value": current})
            except Exception:
                continue
        return snapshot

    async def get_macro_snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_snapshot)

    async def get_economic_calendar(self, days: int = 7) -> list[dict[str, Any]]:
        return [
            {
                "event": "FOMC Meeting",
                "impact": "high",
                "note": "Verify exact dates via economic calendar API (FRED/TradingEconomics)",
            }
        ]
