"""Alert API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from domain.entities import Alert
from services.alert_service import AlertService

router = APIRouter()


def _alert_service(session: AsyncSession) -> AlertService:
    return AlertService(AlertRepository(session), get_settings().alert_cooldown_hours)


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(session: AsyncSession = Depends(get_session)) -> list[Alert]:
    return await _alert_service(session).list_active()


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    ok = await _alert_service(session).acknowledge(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"acknowledged": True}
