"""Health check routes — also wake-path for WhatsApp briefing catch-up."""

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from config.settings import get_settings
from database.engine import get_session
from database.url import is_postgres, is_sqlite, normalize_database_url

router = APIRouter()

_LAST_CATCHUP_MONO: float | None = None
_CATCHUP_MIN_SECONDS = 300  # at most once per 5 minutes via /health


@router.get("/health")
async def health_check() -> dict:
    """Liveness + throttled briefing catch-up (keeps cloud hosts from missing close)."""
    out: dict = {"status": "healthy", "service": "monarch-capital"}
    settings = get_settings()
    if not settings.whatsapp_briefing_enabled:
        return out

    global _LAST_CATCHUP_MONO
    import time as _time

    now = _time.monotonic()
    if _LAST_CATCHUP_MONO is not None and (now - _LAST_CATCHUP_MONO) < _CATCHUP_MIN_SECONDS:
        return out

    try:
        from services.status_briefing_catchup_service import StatusBriefingCatchupService

        async for session in get_session():
            result = await StatusBriefingCatchupService(session).catch_up(via="health_catchup")
            _LAST_CATCHUP_MONO = now
            delivered = {
                k: bool(isinstance(v, dict) and v.get("whatsapp"))
                for k, v in result.items()
                if isinstance(v, dict) and not v.get("skipped")
            }
            if delivered:
                out["briefing_catchup"] = delivered
            break
    except Exception as exc:
        out["briefing_catchup_error"] = str(exc)[:120]
    return out


@router.get("/health/ready")
async def readiness_check() -> dict:
    settings = get_settings()
    url = normalize_database_url(settings.database_url)
    dialect = "postgresql" if is_postgres(url) else ("sqlite" if is_sqlite(url) else "unknown")
    try:
        async for session in get_session():
            await session.execute(text("SELECT 1"))
            return {
                "status": "ready",
                "database": "connected",
                "dialect": dialect,
                "persistent": dialect == "postgresql",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as exc:
        return {
            "status": "not_ready",
            "database": "unavailable",
            "dialect": dialect,
            "persistent": dialect == "postgresql",
            "error": str(exc)[:200],
        }
    return {
        "status": "not_ready",
        "database": "unavailable",
        "dialect": dialect,
        "persistent": dialect == "postgresql",
    }
