"""Factory for macro data providers based on configuration."""

from config.settings import get_settings
from providers.interfaces import MacroProvider
from providers.macro.composite_macro_provider import CompositeMacroProvider
from providers.macro.fred_provider import FredProvider
from providers.macro.yfinance_macro_provider import YFinanceMacroProvider
from utils.logging import get_logger

logger = get_logger(__name__)

_provider: MacroProvider | None = None


def get_macro_provider() -> MacroProvider:
    """Return singleton macro provider (FRED+YFinance if key available, else YFinance only)."""
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    market = YFinanceMacroProvider()

    if settings.fred_api_key:
        logger.info("macro.provider", mode="fred+yfinance")
        _provider = CompositeMacroProvider(FredProvider(settings.fred_api_key), market)
    else:
        logger.warning("macro.provider", mode="yfinance_only", reason="FRED_API_KEY not set")
        _provider = market

    return _provider


def reset_macro_provider() -> None:
    """Reset singleton (for tests)."""
    global _provider
    _provider = None
