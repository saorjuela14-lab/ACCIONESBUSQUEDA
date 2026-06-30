"""Provider diagnostics routes."""

from fastapi import APIRouter

from services.provider_diagnostics import get_providers_status

router = APIRouter()


@router.get("/providers/status")
async def providers_status() -> dict:
    """Diagnose API key auth vs market hours status for all data providers."""
    return await get_providers_status()
