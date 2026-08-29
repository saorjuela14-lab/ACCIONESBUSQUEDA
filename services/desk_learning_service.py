"""Turn evaluated mistakes into durable next-open lessons."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import utc_now
from database.repositories.desk_lesson_repository import DeskLessonRepository
from database.repositories.ops_repository import OpsFlagRepository
from domain.entities import InvestmentMemoryRecord
from utils.logging import get_logger

logger = get_logger(__name__)

FLAG_KEY = "desk_lessons"

_FALSE_LONG = "false_long"
_FALSE_SHORT = "false_short"
_HOLD_MISS = "hold_miss"
_STAGNATION = "stagnation"


def classify_error(
    recommendation: str,
    was_correct: bool,
    *,
    outcome: str | None = None,
) -> str | None:
    if was_correct:
        return None
    if (outcome or "") == "stagnation":
        return _STAGNATION
    rec = (recommendation or "").lower()
    if rec in ("strong_buy", "buy"):
        return _FALSE_LONG
    if rec in ("strong_sell", "sell"):
        return _FALSE_SHORT
    return _HOLD_MISS


class DeskLearningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._lessons = DeskLessonRepository(session)
        self._flags = OpsFlagRepository(session)

    async def ingest_evaluation(
        self,
        record: InvestmentMemoryRecord,
        was_correct: bool,
        *,
        outcome: str | None = None,
    ) -> dict[str, Any] | None:
        tag = classify_error(record.recommendation, was_correct, outcome=outcome)
        if not tag:
            return None
        settings = get_settings()
        try:
            hours = float(settings.memory_avoid_hours or 24)
        except (TypeError, ValueError):
            hours = 24.0
        if tag == _STAGNATION:
            try:
                hours = float(getattr(settings, "memory_stagnation_avoid_hours", 48) or 48)
            except (TypeError, ValueError):
                hours = 48.0
        expires = utc_now() + timedelta(hours=hours)
        ret = record.actual_return_pct
        ret_s = f"{ret:+.1f}%" if ret is not None else "n/d"
        if tag == _STAGNATION:
            reason = (
                f"estancada: {record.ticker} {record.recommendation} → {ret_s}. "
                f"Capital ocupado sin avanzar 1.5%. No repetir el ticker {int(hours)}h."
            )
        else:
            reason = (
                f"{tag}: {record.ticker} {record.recommendation} → {ret_s}. "
                f"No repetir el mismo sesgo en la próxima apertura."
            )
        payload = {
            "memory_id": record.id,
            "recommendation": record.recommendation,
            "actual_return_pct": ret,
            "outcome": outcome,
        }
        if tag in (_FALSE_LONG, _STAGNATION):
            row = await self._lessons.upsert_avoid(
                ticker=record.ticker,
                error_tag=tag,
                reason=reason,
                expires_at=expires,
                recommendation=record.recommendation,
                actual_return_pct=ret,
                memory_id=record.id,
                payload=payload,
            )
            return {"id": row.id, "ticker": record.ticker.upper(), "error_tag": tag, "type": "avoid_ticker"}

        row = await self._lessons.add_note(
            reason=reason,
            expires_at=expires,
            ticker=record.ticker,
            error_tag=tag,
            payload=payload,
        )
        return {"id": row.id, "ticker": record.ticker.upper(), "error_tag": tag, "type": "note"}

    async def ingest_agent_error(
        self,
        *,
        agent_name: str,
        pattern: str,
        reason: str,
        ticker: str | None = None,
        payload: dict | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        try:
            hours = float(getattr(settings, "memory_agent_error_hours", 168) or 168)
        except (TypeError, ValueError):
            hours = 168.0
        expires = utc_now() + timedelta(hours=hours)
        row = await self._lessons.upsert_agent_error(
            agent_name=agent_name,
            pattern=pattern or "false_long",
            reason=reason,
            expires_at=expires,
            ticker=ticker,
            payload=payload,
        )
        logger.info(
            "desk_learning.agent_error",
            agent=agent_name,
            pattern=pattern,
            ticker=ticker,
        )
        return {
            "id": row.id,
            "agent": agent_name,
            "pattern": pattern,
            "ticker": (ticker or "").upper() or None,
            "type": "agent_error",
        }

    async def active_errors_by_agent(self) -> dict[str, list[dict[str, Any]]]:
        rows = await self._lessons.list_active(lesson_type="agent_error")
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            name = (row.agent_name or "").strip()
            if not name:
                continue
            try:
                payload = json.loads(row.payload_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            item = {
                "id": row.id,
                "ticker": row.ticker,
                "pattern": row.error_tag or payload.get("pattern"),
                "reason": row.reason,
                "summary": payload.get("summary") or "",
                "findings": payload.get("findings") or [],
            }
            out.setdefault(name, []).append(item)
        return out

    async def avoid_tickers(self) -> list[str]:
        return await self._lessons.avoid_tickers()

    async def should_avoid(self, ticker: str) -> bool:
        t = (ticker or "").upper().strip()
        if not t:
            return False
        return t in set(await self.avoid_tickers())

    async def latest_for_ticker(self, ticker: str):
        return await self._lessons.latest_for_ticker(ticker)

    async def merge_excludes(self, extra: list[str] | None = None) -> list[str]:
        avoid = await self.avoid_tickers()
        out: list[str] = []
        seen: set[str] = set()
        for raw in list(extra or []) + avoid:
            t = (raw or "").upper().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    async def snapshot(self) -> dict[str, Any]:
        active = await self._lessons.list_active()
        avoids = []
        notes = []
        agent_errors = []
        for row in active:
            item = {
                "id": row.id,
                "ticker": row.ticker,
                "type": row.lesson_type,
                "agent_name": row.agent_name,
                "error_tag": row.error_tag,
                "reason": row.reason,
                "recommendation": row.recommendation,
                "actual_return_pct": row.actual_return_pct,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            if row.lesson_type == "avoid_ticker":
                avoids.append(item)
            elif row.lesson_type == "agent_error":
                agent_errors.append(item)
            else:
                notes.append(item)
        snap = {
            "as_of": utc_now().isoformat(),
            "avoid_tickers": [a["ticker"] for a in avoids if a.get("ticker")],
            "avoids": avoids,
            "agent_errors": agent_errors,
            "notes": notes,
            "count": len(active),
        }
        try:
            await self._flags.set_json(FLAG_KEY, snap)
        except Exception as exc:
            logger.warning("desk_learning.snapshot_flag_failed", error=str(exc))
        return snap

    async def briefing_lines(self) -> list[str]:
        snap = await self.snapshot()
        lines: list[str] = []
        avoid = snap.get("avoid_tickers") or []
        if avoid:
            tagged = []
            for item in snap.get("avoids") or []:
                t = item.get("ticker")
                tag = item.get("error_tag") or "error"
                if t:
                    tagged.append(f"{t} ({tag})")
            lines.append("Lecciones 24h (no repetir): " + ", ".join(tagged[:8] or avoid[:8]))
        for note in (snap.get("notes") or [])[:3]:
            reason = (note.get("reason") or "").strip()
            if reason:
                lines.append(f"· {reason[:160]}")
        for err in (snap.get("agent_errors") or [])[:4]:
            agent = err.get("agent_name") or "agente"
            reason = (err.get("reason") or "").strip()
            if reason:
                lines.append(f"Error {agent}: {reason[:140]}")
        return lines


async def load_lesson_briefing_lines() -> list[str]:
    from database.engine import get_session

    async for session in get_session():
        return await DeskLearningService(session).briefing_lines()
    return []
