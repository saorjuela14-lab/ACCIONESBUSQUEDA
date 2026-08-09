"""Voice assistant API — conversational Viernes + command router + TTS."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from apis.deps import OrgScope, get_org_scope
from database.engine import get_session
from domain.voice import VoiceChatResult, VoiceCommandResult, VoiceHelpItem
from models.schemas import VoiceChatRequest, VoiceCommandRequest, VoiceTTSRequest
from services.elevenlabs_tts_service import ElevenLabsTTSError, ElevenLabsTTSService
from services.voice_assistant_service import VoiceAssistantService
from services.voice_command_service import VoiceCommandService, _HELP_ITEMS

router = APIRouter()


@router.post("/voice/command", response_model=VoiceCommandResult)
async def voice_command(
    request: VoiceCommandRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> VoiceCommandResult:
    """Interpreta texto de voz y ejecuta acciones del panel (comandos rápidos)."""
    org = "monarch" if scope.is_desk else scope.write_org_id()
    return await VoiceCommandService().handle(
        request.text,
        session,
        portfolio_id=request.portfolio_id,
        org_id=org,
    )


@router.post("/voice/chat", response_model=VoiceChatResult)
async def voice_chat(
    request: VoiceChatRequest,
    session: AsyncSession = Depends(get_session),
    scope: OrgScope = Depends(get_org_scope),
) -> VoiceChatResult:
    """Chat conversacional con Viernes (estrategia, simulaciones, ops, cambios de producto)."""
    org = "monarch" if scope.is_desk else scope.write_org_id()
    return await VoiceAssistantService().chat(
        request.text,
        session,
        portfolio_id=request.portfolio_id,
        session_id=request.session_id,
        org_id=org,
    )


@router.get("/voice/assistant/status")
async def voice_assistant_status() -> dict:
    return VoiceAssistantService().status()


@router.get("/voice/cursor/status")
async def voice_cursor_status(scope: OrgScope = Depends(get_org_scope)) -> dict:
    """Estado del puente Viernes → Cursor Cloud Agents (solo mesa)."""
    scope.require_desk()
    from services.cursor_agent_service import CursorAgentService

    return CursorAgentService().status()


@router.get("/voice/help", response_model=list[VoiceHelpItem])
async def voice_help() -> list[VoiceHelpItem]:
    return _HELP_ITEMS


@router.get("/voice/tts/status")
async def voice_tts_status() -> dict:
    """Estado del proveedor TTS (ElevenLabs o fallback del navegador)."""
    return ElevenLabsTTSService().status()


@router.post("/voice/tts")
async def voice_tts(request: VoiceTTSRequest) -> Response:
    """Sintetiza audio con ElevenLabs (voz natural tipo secretaria / Friday)."""
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
