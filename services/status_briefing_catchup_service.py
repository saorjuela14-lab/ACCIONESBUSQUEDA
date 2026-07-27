"""Durable open/close WhatsApp briefing with catch-up after restarts/sleep."""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from database.repositories.ops_repository import OpsFlagRepository
from domain.ops import utc_now
from services.audit_service import AuditService
from services.daily_status_briefing_service import DailyStatusBriefingService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.logging import get_logger
from utils.market_hours import is_trading_day

logger = get_logger(__name__)

ET = ZoneInfo("America/New_York")
SessionKind = Literal["open", "close"]

# Windows: after this local ET time we may catch up until end of trading window
_OPEN_AFTER = time(9, 35)
_CLOSE_AFTER = time(16, 5)
_OPEN_UNTIL = time(15, 30)  # don't send "open" late afternoon
_CLOSE_UNTIL = time(20, 0)


class StatusBriefingCatchupService:
    """Ensures today's open/close briefings are sent even if cron was missed."""

    FLAG_KEY = "status_briefing_sent"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._flags = OpsFlagRepository(session)
        self._audit = AuditService(session)

    def _today_key(self, now: datetime | None = None) -> str:
        now = now or datetime.now(ET)
        return now.astimezone(ET).strftime("%Y-%m-%d")

    async def _state(self) -> dict:
        return await self._flags.get_json(self.FLAG_KEY)

    async def mark_sent(self, kind: SessionKind, *, via: str, result: dict) -> None:
        day = self._today_key()
        state = await self._state()
        day_map = dict(state.get(day) or {})
        day_map[kind] = {
            "sent_at": utc_now().isoformat(),
            "via": via,
            "whatsapp": bool(result.get("whatsapp")),
            "telegram": bool(result.get("telegram")),
            "title": result.get("title"),
        }
        # Keep only last 7 days of keys
        state[day] = day_map
        trimmed = {k: state[k] for k in sorted(state.keys())[-7:]}
        await self._flags.set_json(self.FLAG_KEY, trimmed)

    async def already_sent(self, kind: SessionKind, now: datetime | None = None) -> bool:
        day = self._today_key(now)
        state = await self._state()
        return bool((state.get(day) or {}).get(kind))

    def _due(self, kind: SessionKind, now: datetime) -> bool:
        t = now.astimezone(ET).time()
        if kind == "open":
            return _OPEN_AFTER <= t <= _OPEN_UNTIL
        return _CLOSE_AFTER <= t <= _CLOSE_UNTIL

    async def send_if_needed(
        self,
        kind: SessionKind,
        *,
        via: str = "catchup",
        force: bool = False,
    ) -> dict | None:
        now = datetime.now(ET)
        if not is_trading_day(now):
            return {"skipped": True, "reason": "non_trading_day"}
        if not force and not self._due(kind, now):
            return {"skipped": True, "reason": "outside_window", "kind": kind}
        if not force and await self.already_sent(kind, now):
            return {"skipped": True, "reason": "already_sent", "kind": kind}

        result = await DailyStatusBriefingService().send(kind)
        await self.mark_sent(kind, via=via, result=result)
        await self._audit.record(
            "status_briefing",
            actor=via,
            success=any(result.get(c) for c in ("whatsapp", "telegram", "webhook")),
            message=f"Briefing {kind}: {result.get('title')}",
            payload={k: v for k, v in result.items() if k != "title"},
        )
        logger.info("status_briefing.catchup", kind=kind, via=via, **{
            k: v for k, v in result.items() if k != "title"
        })
        return result

    async def catch_up(self, *, via: str = "catchup") -> dict:
        """Send any missed open/close briefing for today."""
        out: dict = {"open": None, "close": None}
        out["open"] = await self.send_if_needed("open", via=via)
        out["close"] = await self.send_if_needed("close", via=via)
        return out
