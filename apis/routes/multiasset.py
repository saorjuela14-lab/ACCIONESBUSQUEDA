"""Multi-asset beta API — gold / forex / crypto paper desks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from domain.multiasset import AssetDeskId, MultiAssetOrderRequest
from services.multiasset.desk_service import MultiAssetDeskService

router = APIRouter()


def _enabled() -> None:
    if not get_settings().multiasset_beta_enabled:
        raise HTTPException(status_code=503, detail="Módulo multi-asset beta desactivado")


@router.get("/beta/multiasset/desks")
async def list_desks():
    _enabled()
    return {"beta": True, "desks": MultiAssetDeskService().list_desks()}


@router.get("/beta/multiasset/{desk}/status")
async def desk_status(desk: AssetDeskId, session: AsyncSession = Depends(get_session)):
    _enabled()
    try:
        return await MultiAssetDeskService(session).status(desk)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/beta/multiasset/{desk}/brief/{symbol}")
async def desk_brief(desk: AssetDeskId, symbol: str, session: AsyncSession = Depends(get_session)):
    _enabled()
    try:
        return await MultiAssetDeskService(session).brief(desk, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/beta/multiasset/execute")
async def execute_order(
    body: MultiAssetOrderRequest,
    session: AsyncSession = Depends(get_session),
):
    _enabled()
    try:
        return await MultiAssetDeskService(session).execute(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Orden falló: {exc}") from exc


@router.get("/beta/multiasset/history")
async def history(
    desk: AssetDeskId | None = None,
    limit: int = Query(default=40, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    _enabled()
    items = await MultiAssetDeskService(session).history(desk=desk, limit=limit)
    return {"items": items, "count": len(items)}
