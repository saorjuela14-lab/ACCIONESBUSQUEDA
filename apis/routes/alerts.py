"""Alert API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.engine import get_session
from database.repositories.alert_repository import AlertRepository
from domain.entities import Alert
from domain.enums import AlertSeverity, AlertType
from services.alert_service import AlertService
from services.daily_status_briefing_service import DailyStatusBriefingService
from services.push_notification_service import PushNotificationService

router = APIRouter()


def _alert_service(session: AsyncSession) -> AlertService:
    return AlertService(AlertRepository(session), get_settings().alert_cooldown_hours)


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(session: AsyncSession = Depends(get_session)) -> list[Alert]:
    return await _alert_service(session).list_active()


@router.get("/alerts/push-status")
async def alert_push_status() -> dict:
    """Indica si Telegram/WhatsApp/webhook están configurados para push."""
    return PushNotificationService().status()


@router.post("/alerts/test-push")
async def test_push_notification() -> dict:
    """Envía alerta de prueba a canales configurados."""
    push = PushNotificationService()
    if not push.any_channel_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "Configura TELEGRAM_BOT_TOKEN+CHAT_ID y/o WhatsApp "
                "(CallMeBot / Meta / Twilio) o ALERT_WEBHOOK_URL"
            ),
        )
    sample = Alert(
        ticker="TEST",
        alert_type=AlertType.BREAKOUT,
        severity=AlertSeverity.MEDIUM,
        title="Alerta de prueba NexBuy",
        description="Si ves esto, las notificaciones push están activas.",
    )
    result = await push.notify_alert(sample)
    return {"sent": result, "ok": any(result.values())}


@router.post("/alerts/briefing/send")
async def send_status_briefing(
    session_kind: str = Query(default="manual", pattern="^(open|close|manual)$"),
    whatsapp_only: bool = Query(default=False),
) -> dict:
    """Envía ahora el status de portafolio/órdenes (apertura, cierre o manual)."""
    push = PushNotificationService()
    if whatsapp_only and not push.whatsapp_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "WhatsApp no configurado. Usa CallMeBot (WHATSAPP_PHONE+WHATSAPP_API_KEY) "
                "o Meta (WHATSAPP_TOKEN+WHATSAPP_PHONE_NUMBER_ID+WHATSAPP_TO) "
                "o Twilio (TWILIO_* + WHATSAPP_TO)."
            ),
        )
    if not whatsapp_only and not push.any_channel_configured:
        raise HTTPException(status_code=400, detail="Ningún canal push configurado")
    result = await DailyStatusBriefingService().send(
        session_kind,  # type: ignore[arg-type]
        whatsapp_only=whatsapp_only,
    )
    return {"ok": any(v for k, v in result.items() if k != "title" and v is True), "result": result}


@router.get("/alerts/briefing/preview")
async def preview_status_briefing(
    session_kind: str = Query(default="manual", pattern="^(open|close|manual)$"),
) -> dict:
    """Vista previa del texto del briefing (no envía)."""
    title, body = await DailyStatusBriefingService().build(session_kind)  # type: ignore[arg-type]
    return {"title": title, "body": body}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    ok = await _alert_service(session).acknowledge(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"acknowledged": True}
