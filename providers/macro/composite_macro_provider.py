"""Composite macro provider: FRED (verified fundamentals) + YFinance (market proxies)."""

from typing import Any

from providers.interfaces import MacroProvider
from providers.macro.fred_provider import FredProvider
from providers.macro.yfinance_macro_provider import YFinanceMacroProvider


class CompositeMacroProvider(MacroProvider):
    """
    Merges FRED official economic data with YFinance market proxies.
    FRED takes precedence for overlapping monetary/rate indicators.
    """

    def __init__(self, fred_provider: FredProvider, market_provider: YFinanceMacroProvider) -> None:
        self._fred = fred_provider
        self._market = market_provider

    async def get_macro_snapshot(self) -> dict[str, Any]:
        fred_data = await self._fred.get_macro_snapshot()
        market_data = await self._market.get_macro_snapshot()

        indicators = dict(market_data.get("indicators", {}))
        fred_indicators = fred_data.get("indicators", {})

        # FRED overrides YFinance for rates and VIX when available
        fred_overrides = {"YIELD_10Y", "VIX", "FED_FUNDS", "YIELD_2Y", "YIELD_CURVE"}
        for key, data in fred_indicators.items():
            if key in fred_overrides or key not in indicators:
                # Map FRED keys to market monitor keys where applicable
                mapped_key = {
                    "YIELD_10Y": "US10Y",
                    "VIX": "VIX",
                }.get(key, key)
                indicators[mapped_key] = {
                    "symbol": data.get("series_id"),
                    "value": data["value"],
                    "change_pct": data.get("change_pct"),
                    "source": "fred",
                    "label": data.get("label"),
                    "date": data.get("date"),
                    "unit": data.get("unit"),
                }

        references = fred_data.get("references", []) + market_data.get("references", [])

        return {
            "indicators": indicators,
            "fred": fred_indicators,
            "market": market_data.get("indicators", {}),
            "references": references,
            "providers": ["fred", "yfinance"],
            "fetched_at": fred_data.get("fetched_at"),
        }

    async def get_economic_calendar(self, days: int = 7) -> list[dict[str, Any]]:
        return await self._fred.get_economic_calendar(days)
