"""Watchlist API routes (org-scoped)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
from config.settings import get_settings
from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from database.repositories.watchlist_repository import WatchlistRepository
from database.repositories.watchlist_snapshot_repository import WatchlistSnapshotRepository
from domain.entities import WatchlistItem
from models.schemas import WatchlistAddRequest
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from services.alert_service import AlertService
from services.watchlist_monitor_service import WatchlistMonitorService
from services.watchlist_service import WatchlistService

router = APIRouter()


def _build_service(session: AsyncSession) -> WatchlistService:
    return WatchlistService(WatchlistRepository(session), get_market_provider())


def _build_monitor(session: AsyncSession, org_id: str | None) -> WatchlistMonitorService:
    settings = get_settings()
    return WatchlistMonitorService(
        WatchlistRepository(session),
        WatchlistSnapshotRepository(session),
        AlertService(AlertRepository(session), settings.alert_cooldown_hours),
        get_market_provider(),
        get_news_provider(),
        org_id=org_id,
    )


@router.get("/watchlist", response_model=list[WatchlistItem])
async def list_watchlist(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> list[WatchlistItem]:
    return await _build_service(session).list_active(org_id=scope.read_org_id())


@router.post("/watchlist", response_model=WatchlistItem)
async def add_to_watchlist(
    request: WatchlistAddRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> WatchlistItem:
    return await _build_service(session).add(
        request.ticker, notes=request.notes, org_id=scope.write_org_id()
    )


@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict[str, bool]:
    removed = await _build_service(session).remove(ticker, org_id=scope.read_org_id())
    if not removed:
        raise HTTPException(status_code=404, detail="Ticker not on watchlist")
    return {"removed": True}


@router.post("/watchlist/scan")
async def scan_watchlist(
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> dict:
    """Manually trigger watchlist monitoring scan for this org."""
    return await _build_monitor(session, scope.book_org_id()).scan_all()
