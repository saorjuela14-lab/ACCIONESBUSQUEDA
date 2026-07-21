"""Provider interfaces following repository pattern."""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from domain.enums import NewsTopicCategory
from domain.reports import NewsItem
from domain.sentiment import SentimentSnapshot


class MarketDataProvider(ABC):
    @abstractmethod
    async def get_quote(self, ticker: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_history(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    async def get_financials(self, ticker: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_peers(self, ticker: str) -> list[str]:
        raise NotImplementedError


class NewsProvider(ABC):
    @abstractmethod
    async def search_news(
        self,
        query: str,
        max_results: int = 10,
        hint_category: NewsTopicCategory | None = None,
    ) -> list[NewsItem]:
        raise NotImplementedError


class MacroProvider(ABC):
    @abstractmethod
    async def get_macro_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_economic_calendar(self, days: int = 7) -> list[dict[str, Any]]:
        raise NotImplementedError


class SentimentProvider(ABC):
    @abstractmethod
    async def get_sentiment(self, ticker: str, company_name: str | None = None) -> SentimentSnapshot:
        raise NotImplementedError


class BrokerProvider(ABC):
    """Broker execution interface (Alpaca Trading API)."""

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_account(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_orders(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def close_position(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_clock(self) -> dict[str, Any]:
        raise NotImplementedError
