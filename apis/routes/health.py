"""Health check routes — also wake-path for WhatsApp briefing catch-up."""

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from config.settings import get_settings
from database.engine import db_snapshot, get_session
from database.url import is_postgres, is_sqlite, normalize_database_url, sanitize_db_error
from utils.metrics import metrics

router = APIRouter()

_LAST_CATCHUP_MONO: float | None = None
_CATCHUP_MIN_SECONDS = 300  # at most once per 5 minutes via /health


@router.get("/health")
async def health_check() -> dict:
    """Liveness — always 200 if the process is up (DB may be down / reconnecting)."""
    snap = db_snapshot()
    out: dict = {
        "status": "healthy",
        "service": "monarch-capital",
        "db": "up" if snap["ready"] else "down",
    }
    if not snap["ready"] and snap.get("error"):
        out["db_error"] = snap["error"]
    settings = get_settings()
    from services.live_safety import trading_mode_label

    mode = trading_mode_label(settings)
    out["trading_mode"] = mode
    if mode == "unconfigured":
        out["trading_mode_unconfigured"] = True
    if not snap["ready"] or not settings.whatsapp_briefing_enabled:
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
        out["briefing_catchup_error"] = sanitize_db_error(exc)
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
            "error": sanitize_db_error(exc),
        }
    return {
        "status": "not_ready",
        "database": "unavailable",
        "dialect": dialect,
        "persistent": dialect == "postgresql",
    }


@router.get("/metrics")
async def metrics_snapshot() -> dict:
    """Simple in-process counters (auth failures, HTTP codes, client errors)."""
    snap = metrics.snapshot()
    settings = get_settings()
    return {
        "service": "monarch-capital",
        "env": settings.app_env,
        **snap,
    }
