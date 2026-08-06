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
async def alert_push_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Indica si Telegram/WhatsApp/webhook están configurados + briefings enviados hoy."""
    status = PushNotificationService().status()
    try:
        from services.status_briefing_catchup_service import StatusBriefingCatchupService

        svc = StatusBriefingCatchupService(session)
        day = svc._today_key()
        state = await svc._state()
        status["briefing_day"] = day
        status["briefing_sent_today"] = dict(state.get(day) or {})
    except Exception as exc:
        status["briefing_sent_today_error"] = str(exc)[:120]
    return status


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
        title="Alerta de prueba Monarch Capital",
        description="Si ves esto, las notificaciones push están activas.",
    )
    result = await push.notify_alert(sample)
    return {"sent": result, "ok": any(result.values())}


@router.post("/alerts/briefing/send")
async def send_status_briefing(
    session_kind: str = Query(default="manual", pattern="^(open|lunch|close|manual)$"),
    whatsapp_only: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Envía ahora el status de portafolio/órdenes (apertura, almuerzo, cierre o manual)."""
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

    if session_kind in ("open", "lunch", "close") and not whatsapp_only:
        from services.status_briefing_catchup_service import StatusBriefingCatchupService

        result = await StatusBriefingCatchupService(session).send_if_needed(
            session_kind,  # type: ignore[arg-type]
            via="api_manual",
            force=True,
        )
        return {
            "ok": bool(result and any(result.get(c) for c in ("whatsapp", "telegram", "webhook"))),
            "result": result,
        }

    result = await DailyStatusBriefingService().send(
        session_kind,  # type: ignore[arg-type]
        whatsapp_only=whatsapp_only,
    )
    if session_kind in ("open", "lunch", "close"):
        from services.status_briefing_catchup_service import StatusBriefingCatchupService

        await StatusBriefingCatchupService(session).mark_sent(
            session_kind,  # type: ignore[arg-type]
            via="api_manual",
            result=result,
        )
    return {"ok": any(v for k, v in result.items() if k != "title" and v is True), "result": result}


@router.get("/alerts/briefing/preview")
async def preview_status_briefing(
    session_kind: str = Query(default="manual", pattern="^(open|lunch|close|manual)$"),
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
