"""Simple access token auth (no OpenAI / OAuth required)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import get_settings

router = APIRouter()


class LoginRequest(BaseModel):
    token: str


@router.get("/auth/status")
async def auth_status() -> dict:
    settings = get_settings()
    return {
        "auth_required": bool(settings.dashboard_access_token),
        "app_name": settings.app_name,
    }


@router.post("/auth/login")
async def login(request: LoginRequest) -> dict:
    settings = get_settings()
    if not settings.dashboard_access_token:
        return {"ok": True, "message": "Auth desactivado en este servidor"}
    if request.token != settings.dashboard_access_token:
        raise HTTPException(status_code=401, detail="Token incorrecto")
    return {"ok": True}
