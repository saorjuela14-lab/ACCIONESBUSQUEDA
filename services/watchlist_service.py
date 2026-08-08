"""Watchlist management service."""

from database.repositories.watchlist_repository import WatchlistRepository
from domain.entities import WatchlistItem
from providers.interfaces import MarketDataProvider


class WatchlistService:
    def __init__(self, repo: WatchlistRepository, market_provider: MarketDataProvider) -> None:
        self._repo = repo
        self._market = market_provider

    async def add(
        self, ticker: str, notes: str | None = None, org_id: str | None = None
    ) -> WatchlistItem:
        quote = await self._market.get_quote(ticker)
        return await self._repo.add(
            ticker,
            company_name=quote.get("company_name"),
            notes=notes,
            org_id=org_id,
        )

    async def list_active(self, org_id: str | None = None) -> list[WatchlistItem]:
        return await self._repo.list_active(org_id=org_id)

    async def remove(self, ticker: str, org_id: str | None = None) -> bool:
        return await self._repo.remove(ticker, org_id=org_id)
