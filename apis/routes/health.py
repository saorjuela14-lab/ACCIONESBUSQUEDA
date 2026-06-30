"""Health check routes."""

from fastapi import APIRouter
from sqlalchemy import text

from database.engine import get_session

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "nexbuy-investment-committee"}


@router.get("/health/ready")
async def readiness_check() -> dict[str, str]:
    async for session in get_session():
        await session.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    return {"status": "not_ready", "database": "unavailable"}
