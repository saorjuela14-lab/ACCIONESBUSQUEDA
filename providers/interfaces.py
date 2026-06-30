"""Provider interfaces following repository pattern."""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from domain.reports import NewsItem


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
    async def search_news(self, query: str, max_results: int = 10) -> list[NewsItem]:
        raise NotImplementedError


class MacroProvider(ABC):
    @abstractmethod
    async def get_macro_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_economic_calendar(self, days: int = 7) -> list[dict[str, Any]]:
        raise NotImplementedError
