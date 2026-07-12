"""Voice assistant API — interpret spoken commands and execute actions."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from domain.voice import VoiceCommandResult, VoiceHelpItem
from models.schemas import VoiceCommandRequest
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
