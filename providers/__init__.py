"""External data providers."""

from providers.interfaces import MacroProvider, MarketDataProvider, NewsProvider
from providers.market.factory import get_market_provider
from providers.market.yfinance_provider import YFinanceProvider
from providers.news.duckduckgo_provider import DuckDuckGoNewsProvider

__all__ = [
    "DuckDuckGoNewsProvider",
    "MacroProvider",
    "MarketDataProvider",
    "NewsProvider",
    "YFinanceProvider",
    "get_market_provider",
]
