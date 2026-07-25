"""Bootstrap durable flags when firm autonomy is authorized."""

from __future__ import annotations

from database.repositories.ops_repository import OpsFlagRepository
from domain.ops import utc_now
from services.audit_service import AuditService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger

logger = get_logger(__name__)


async def ensure_firm_autonomy_flags(session: AsyncSession, *, firm_autonomy: bool) -> dict:
    """Persist paper→LIVE promotion when the owner authorized full autonomy."""
    if not firm_autonomy:
        return {"promoted": False, "skipped": True}

    repo = OpsFlagRepository(session)
    existing = await repo.get_json("paper_promotion")
    if existing.get("promoted"):
        return existing

    payload = {
        "promoted": True,
        "promoted_at": utc_now().isoformat(),
        "note": "FIRM_AUTONOMY=true — owner authorized independent LIVE desk",
        "paper_fills_seen": existing.get("paper_fills_seen", 0),
        "source": "firm_autonomy_bootstrap",
    }
    await repo.set_json("paper_promotion", payload)
    await AuditService(session).record(
        "auto_execute",
        actor="system",
        success=True,
        message="Firm autonomy: paper→LIVE promotion flag set on startup",
        payload=payload,
    )
    logger.info("firm_autonomy.promoted", **{k: payload[k] for k in ("promoted_at", "note")})
    return payload
