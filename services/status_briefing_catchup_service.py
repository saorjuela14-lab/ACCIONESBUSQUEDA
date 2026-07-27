"""Durable open/lunch/close WhatsApp briefing with silent catch-up after restarts.

You only receive **3 messages per trading day** (open, lunch, close).
The catch-up job never spams: it only sends if that slot was missed and not yet marked sent.

Cold-start / sleep: if the host woke after the cron minute, any overdue slot
still due before end-of-desk (18:30 ET) is sent once — so a 13:00 wake still
delivers missed open + lunch.
"""

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
SessionKind = Literal["open", "lunch", "close"]

# Scheduled times (ET). Catch-up is due from this time until desk end.
_SLOT_AT: dict[SessionKind, time] = {
    "open": time(9, 35),
    "lunch": time(12, 30),
    "close": time(16, 5),
}
_DESK_END = time(18, 30)


class StatusBriefingCatchupService:
    """Ensures today's 3 briefings are sent even if cron was missed — never more than once each."""

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
        state[day] = day_map
        trimmed = {k: state[k] for k in sorted(state.keys())[-7:]}
        await self._flags.set_json(self.FLAG_KEY, trimmed)

    async def already_sent(self, kind: SessionKind, now: datetime | None = None) -> bool:
        day = self._today_key(now)
        state = await self._state()
        return bool((state.get(day) or {}).get(kind))

    def _due(self, kind: SessionKind, now: datetime) -> bool:
        """True if slot time has passed today and desk is still active (≤18:30 ET)."""
        t = now.astimezone(ET).time()
        if t > _DESK_END:
            return False
        return t >= _SLOT_AT[kind]

    @staticmethod
    def _delivered(result: dict) -> bool:
        return any(result.get(c) for c in ("whatsapp", "telegram", "webhook"))

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
        delivered = self._delivered(result)
        if delivered or force:
            # Only durable-mark on success (or explicit force) so failed WA can retry
            await self.mark_sent(kind, via=via, result=result)
        await self._audit.record(
            "status_briefing",
            actor=via,
            success=delivered,
            message=f"Briefing {kind}: {result.get('title')}",
            payload={k: v for k, v in result.items() if k != "title"},
        )
        logger.info(
            "status_briefing.sent" if delivered else "status_briefing.failed",
            kind=kind,
            via=via,
            **{k: v for k, v in result.items() if k != "title"},
        )
        return result

    async def catch_up(self, *, via: str = "catchup") -> dict:
        """Send at most the overdue slots for today (0–3 messages, usually 0)."""
        out: dict = {}
        for kind in ("open", "lunch", "close"):
            out[kind] = await self.send_if_needed(kind, via=via)  # type: ignore[arg-type]
        return out
