"""Resilient composite market data provider with automatic fallback."""

from typing import Any, Callable, Coroutine

import pandas as pd

from config.settings import get_settings
from providers.interfaces import MarketDataProvider
from providers.market.alpha_vantage_provider import AlphaVantageProvider
from providers.market.alpaca_provider import AlpacaMarketDataProvider
from providers.market.intervals import is_history_stale, last_bar_timestamp
from providers.market.polygon_provider import PolygonProvider
from providers.market.rate_limit_tracker import get_rate_limit_tracker
from providers.market.yfinance_provider import YFinanceProvider
from utils.logging import get_logger

logger = get_logger(__name__)

ProviderCall = Callable[[], Coroutine[Any, Any, Any]]


class CompositeMarketDataProvider(MarketDataProvider):
    """
    Tries Alpaca → Polygon → Alpha Vantage → YFinance for each request.
    Skips providers that exhausted their rate limit or fail.
    """

    name = "composite"

    # Priority order for price/history data
    HISTORY_CHAIN = ("alpaca", "polygon", "alpha_vantage", "yfinance")
    QUOTE_CHAIN = ("alpaca", "polygon", "alpha_vantage", "yfinance")
    # Fundamentals: YFinance first (richest free data), then Alpha Vantage overview
    FINANCIALS_CHAIN = ("yfinance", "alpha_vantage")

    def __init__(
        self,
        alpaca: AlpacaMarketDataProvider | None = None,
        polygon: PolygonProvider | None = None,
        alpha_vantage: AlphaVantageProvider | None = None,
        yfinance: YFinanceProvider | None = None,
    ) -> None:
        settings = get_settings()
        self._tracker = get_rate_limit_tracker()
        self._polygon_daily = settings.polygon_daily_limit
        self._polygon_per_minute = settings.polygon_per_minute_limit
        self._alpha_daily = settings.alpha_vantage_daily_limit

        self._providers: dict[str, MarketDataProvider] = {
            "yfinance": yfinance or YFinanceProvider(),
        }

        if alpaca:
            self._providers["alpaca"] = alpaca
        elif settings.alpaca_api_key and settings.alpaca_secret_key:
            try:
                self._providers["alpaca"] = AlpacaMarketDataProvider()
            except ValueError:
                pass

        if polygon:
            self._providers["polygon"] = polygon
        elif settings.polygon_api_key:
            try:
                self._providers["polygon"] = PolygonProvider(settings.polygon_api_key)
            except ValueError:
                pass

        if alpha_vantage:
            self._providers["alpha_vantage"] = alpha_vantage
        elif settings.alpha_vantage_api_key:
            try:
                self._providers["alpha_vantage"] = AlphaVantageProvider(settings.alpha_vantage_api_key)
            except ValueError:
                pass

        if not settings.yfinance_enabled:
            self._providers.pop("yfinance", None)

        logger.info(
            "market.provider.chain",
            providers=list(self._providers.keys()),
        )

    def _limits(self, provider_name: str) -> tuple[int | None, int | None]:
        if provider_name == "polygon":
            return self._polygon_daily, self._polygon_per_minute
        if provider_name == "alpha_vantage":
            return self._alpha_daily, None
        return None, None

    async def _try_chain(
        self,
        chain: tuple[str, ...],
        operation: str,
        ticker: str,
        fetchers: dict[str, ProviderCall],
    ) -> Any:
        errors: list[str] = []

        for provider_name in chain:
            if provider_name not in self._providers:
                continue
            if provider_name not in fetchers:
                continue

            daily_limit, per_minute_limit = self._limits(provider_name)
            if not self._tracker.can_request(provider_name, daily_limit, per_minute_limit):
                errors.append(f"{provider_name}: rate limit exhausted")
                continue

            try:
                result = await fetchers[provider_name]()
                self._tracker.record(provider_name)

                if isinstance(result, pd.DataFrame) and result.empty:
                    errors.append(f"{provider_name}: empty dataframe")
                    continue

                if result is None:
                    errors.append(f"{provider_name}: null result")
                    continue

                logger.info(
                    "market.provider.success",
                    operation=operation,
                    ticker=ticker,
                    provider=provider_name,
                )
                if isinstance(result, dict) and "source" not in result:
                    result["source"] = provider_name
                return result

            except NotImplementedError:
                errors.append(f"{provider_name}: not implemented for {operation}")
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                # Treat 429 as rate limit exhaustion for this provider
                if "429" in str(exc):
                    daily_limit, per_minute_limit = self._limits(provider_name)
                    for _ in range(per_minute_limit or 5):
                        self._tracker.record(provider_name)
                logger.warning(
                    "market.provider.failed",
                    operation=operation,
                    ticker=ticker,
                    provider=provider_name,
                    error=str(exc),
                )

        logger.error("market.provider.all_failed", operation=operation, ticker=ticker, errors=errors)
        if operation == "get_history":
            return pd.DataFrame()
        if operation == "get_peers":
            return []
        raise RuntimeError(f"All providers failed for {operation}({ticker}): {'; '.join(errors)}")

    async def get_quote(self, ticker: str) -> dict[str, Any]:
        fetchers = {name: (lambda p=name: self._providers[p].get_quote(ticker)) for name in self._providers}
        quote = await self._try_chain(self.QUOTE_CHAIN, "get_quote", ticker, fetchers)

        # Enrich quote with YFinance metadata if missing sector/company
        if quote.get("sector") is None and "yfinance" in self._providers:
            try:
                yf_quote = await self._providers["yfinance"].get_quote(ticker)
                quote.setdefault("company_name", yf_quote.get("company_name"))
                quote.setdefault("sector", yf_quote.get("sector"))
                quote.setdefault("industry", yf_quote.get("industry"))
                quote.setdefault("country", yf_quote.get("country"))
                if quote.get("market_cap") is None:
                    quote["market_cap"] = yf_quote.get("market_cap")
            except Exception:
                pass

        return quote

    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetch history, preferring a fresh series when the first provider is stale.

        Delisted symbols often still return old bars from one vendor; we keep
        trying the chain for a newer last bar, then return the freshest series.
        """
        errors: list[str] = []
        best: pd.DataFrame | None = None
        best_ts = None

        for provider_name in self.HISTORY_CHAIN:
            if provider_name not in self._providers:
                continue

            daily_limit, per_minute_limit = self._limits(provider_name)
            if not self._tracker.can_request(provider_name, daily_limit, per_minute_limit):
                errors.append(f"{provider_name}: rate limit exhausted")
                continue

            try:
                result = await self._providers[provider_name].get_history(ticker, period, interval)
                self._tracker.record(provider_name)

                if not isinstance(result, pd.DataFrame) or result.empty:
                    errors.append(f"{provider_name}: empty dataframe")
                    continue

                ts = last_bar_timestamp(result)
                if best is None or (ts is not None and (best_ts is None or ts > best_ts)):
                    best = result
                    best_ts = ts

                if not is_history_stale(result, interval):
                    logger.info(
                        "market.provider.success",
                        operation="get_history",
                        ticker=ticker,
                        provider=provider_name,
                        fresh=True,
                    )
                    return result

                errors.append(f"{provider_name}: stale last bar {ts}")
                logger.info(
                    "market.provider.stale",
                    operation="get_history",
                    ticker=ticker,
                    provider=provider_name,
                    last_bar=str(ts),
                )

            except NotImplementedError:
                errors.append(f"{provider_name}: not implemented for get_history")
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                if "429" in str(exc):
                    for _ in range(per_minute_limit or 5):
                        self._tracker.record(provider_name)
                logger.warning(
                    "market.provider.failed",
                    operation="get_history",
                    ticker=ticker,
                    provider=provider_name,
                    error=str(exc),
                )

        if best is not None:
            logger.info(
                "market.provider.success",
                operation="get_history",
                ticker=ticker,
                provider="freshest_stale",
                last_bar=str(best_ts),
            )
            return best

        logger.error("market.provider.all_failed", operation="get_history", ticker=ticker, errors=errors)
        return pd.DataFrame()

    async def get_financials(self, ticker: str) -> dict[str, Any]:
        fetchers = {
            name: (lambda p=name: self._providers[p].get_financials(ticker))
            for name in self._providers
            if name in self.FINANCIALS_CHAIN
        }
        try:
            return await self._try_chain(self.FINANCIALS_CHAIN, "get_financials", ticker, fetchers)
        except RuntimeError:
            return {"info": {}, "income_stmt": {}, "balance_sheet": {}, "cashflow": {}}

    async def get_peers(self, ticker: str) -> list[str]:
        if "yfinance" in self._providers:
            try:
                return await self._providers["yfinance"].get_peers(ticker)
            except Exception:
                pass
        return []

    def usage_stats(self) -> dict[str, dict[str, int]]:
        return {name: self._tracker.get_usage(name) for name in self._providers}
