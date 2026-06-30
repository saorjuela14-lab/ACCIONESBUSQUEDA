"""FRED series definitions for US macroeconomic indicators."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FredSeriesDef:
    series_id: str
    label: str
    unit: str
    category: str  # monetary | inflation | employment | growth | rates | liquidity


# Verified FRED series IDs (Federal Reserve Economic Data)
FRED_SERIES: dict[str, FredSeriesDef] = {
    "FED_FUNDS": FredSeriesDef("FEDFUNDS", "Federal Funds Effective Rate", "%", "monetary"),
    "CPI": FredSeriesDef("CPIAUCSL", "Consumer Price Index (All Urban)", "index", "inflation"),
    "UNEMPLOYMENT": FredSeriesDef("UNRATE", "Unemployment Rate", "%", "employment"),
    "GDP": FredSeriesDef("GDP", "Gross Domestic Product", "billions USD", "growth"),
    "YIELD_10Y": FredSeriesDef("DGS10", "10-Year Treasury Constant Maturity", "%", "rates"),
    "YIELD_2Y": FredSeriesDef("DGS2", "2-Year Treasury Constant Maturity", "%", "rates"),
    "YIELD_CURVE": FredSeriesDef("T10Y2Y", "10Y minus 2Y Treasury Spread", "%", "rates"),
    "M2": FredSeriesDef("M2SL", "M2 Money Stock", "billions USD", "liquidity"),
    "INDUSTRIAL_PRODUCTION": FredSeriesDef("INDPRO", "Industrial Production Index", "index", "growth"),
    "CONSUMER_SENTIMENT": FredSeriesDef("UMCSENT", "U. of Michigan Consumer Sentiment", "index", "growth"),
    "VIX": FredSeriesDef("VIXCLS", "CBOE Volatility Index", "index", "risk"),
}

# Releases to monitor for economic calendar
FRED_RELEASE_IDS = {
    10: "Consumer Price Index",
    50: "Employment Situation",
    53: "Gross Domestic Product",
    101: "FOMC Press Release",
    180: "Industrial Production and Capacity Utilization",
}
