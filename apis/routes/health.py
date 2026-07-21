"""Health check routes."""

from fastapi import APIRouter
from sqlalchemy import text

from config.settings import get_settings
from database.engine import get_session
from database.url import is_postgres, is_sqlite, normalize_database_url

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "nexbuy-investment-committee"}


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
