"""Watchlist API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.watchlist_repository import WatchlistRepository
from domain.entities import WatchlistItem
from models.schemas import WatchlistAddRequest
from providers.market.yfinance_provider import YFinanceProvider
from services.watchlist_service import WatchlistService

router = APIRouter()


def _build_service(session: AsyncSession) -> WatchlistService:
    return WatchlistService(WatchlistRepository(session), YFinanceProvider())


@router.get("/watchlist", response_model=list[WatchlistItem])
async def list_watchlist(session: AsyncSession = Depends(get_session)) -> list[WatchlistItem]:
    return await _build_service(session).list_active()


@router.post("/watchlist", response_model=WatchlistItem)
async def add_to_watchlist(
    request: WatchlistAddRequest,
    session: AsyncSession = Depends(get_session),
) -> WatchlistItem:
    return await _build_service(session).add(request.ticker, notes=request.notes)


@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    removed = await _build_service(session).remove(ticker)
    if not removed:
        raise HTTPException(status_code=404, detail="Ticker not on watchlist")
    return {"removed": True}
