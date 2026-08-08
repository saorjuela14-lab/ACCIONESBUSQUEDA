"""Voice assistant API — interpret spoken commands and execute actions."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from domain.voice import VoiceCommandResult, VoiceHelpItem
from models.schemas import VoiceCommandRequest, VoiceTTSRequest
from services.elevenlabs_tts_service import ElevenLabsTTSError, ElevenLabsTTSService
from services.voice_command_service import VoiceCommandService, _HELP_ITEMS

router = APIRouter()


@router.post("/voice/command", response_model=VoiceCommandResult)
async def voice_command(
    request: VoiceCommandRequest,
    session: AsyncSession = Depends(get_session),
) -> VoiceCommandResult:
    """Interpreta texto de voz y ejecuta acciones del panel."""
    return await VoiceCommandService().handle(
        request.text,
        session,
        portfolio_id=request.portfolio_id,
    )


@router.get("/voice/help", response_model=list[VoiceHelpItem])
async def voice_help() -> list[VoiceHelpItem]:
    return _HELP_ITEMS


@router.get("/voice/tts/status")
async def voice_tts_status() -> dict:
    """Estado del proveedor TTS (ElevenLabs o fallback del navegador)."""
    return ElevenLabsTTSService().status()


@router.post("/voice/tts")
async def voice_tts(request: VoiceTTSRequest) -> Response:
    """Sintetiza audio con ElevenLabs (voz amigable tipo secretaria / Friday)."""
    svc = ElevenLabsTTSService()
    if not svc.configured():
        status = svc.status()
        raise HTTPException(
            status_code=503,
            detail=status.get("hint")
            or "ElevenLabs no configurado. Define ELEVENLABS_API_KEY (sk_...) en el entorno.",
        )
    try:
        audio, content_type = await svc.synthesize(request.text)
    except ElevenLabsTTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — never crash the worker on TTS
        raise HTTPException(status_code=502, detail=f"TTS falló: {exc}") from exc
    return Response(
        content=audio,
        media_type=content_type or "audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
