"""Factory for resilient market data provider chain."""

from config.settings import get_settings
from providers.interfaces import MarketDataProvider
from providers.market.composite_market_provider import CompositeMarketDataProvider
from providers.market.yfinance_provider import YFinanceProvider
from utils.logging import get_logger

logger = get_logger(__name__)

_provider: MarketDataProvider | None = None


def get_market_provider() -> MarketDataProvider:
    """Return singleton composite provider (Polygon → Alpha Vantage → YFinance)."""
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    has_premium = bool(settings.polygon_api_key or settings.alpha_vantage_api_key)

    if has_premium or settings.yfinance_enabled:
        logger.info(
            "market.provider",
            mode="composite",
            polygon=bool(settings.polygon_api_key),
            alpha_vantage=bool(settings.alpha_vantage_api_key),
            yfinance=settings.yfinance_enabled,
        )
        _provider = CompositeMarketDataProvider()
    else:
        logger.warning("market.provider", mode="yfinance_only")
        _provider = YFinanceProvider()

    return _provider


def reset_market_provider() -> None:
    global _provider
    _provider = None
