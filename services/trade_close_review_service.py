"""Grade committee members against realized P&L when a LIVE trade closes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from database.repositories.ops_repository import OpsFlagRepository
from database.repositories.trade_journal_repository import TradeJournalRepository
from domain.trade_journal import TradeJournalEntry
from services.desk_learning_service import DeskLearningService, classify_error
from services.memory_evaluation_service import recalibrate_agent_weights
from utils.logging import get_logger
from utils.narrative_es import agent_display_name

logger = get_logger(__name__)

FLAG_KEY = "last_close_review"
_SCORE_THRESHOLD = 5.0


class TradeCloseReviewService:
    """Authoritative member scorecard: closed trade P&L, not mark-to-market."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._memory = InvestmentMemoryRepository(session)
        self._journal = TradeJournalRepository(session)
        self._learn = DeskLearningService(session)
        self._flags = OpsFlagRepository(session)

    async def latest(self) -> dict[str, Any]:
        return await self._flags.get_json(FLAG_KEY) or {}

    async def review_unreviewed_closes(self, *, days: int = 14) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        closed = await self._journal.list_closed(limit=80, days=days)
        for entry in closed:
            if (entry.meta or {}).get("member_review"):
                continue
            if entry.status != "closed" or entry.pnl_pct is None:
                continue
            review = await self.review_closed(entry)
            if review:
                out.append(review)
        return out

    async def review_closed(self, entry: TradeJournalEntry) -> dict[str, Any] | None:
        if not entry or entry.status != "closed":
            return None
        existing = (entry.meta or {}).get("member_review")
        if isinstance(existing, dict) and existing.get("symbol"):
            return existing

        pnl = float(entry.pnl_pct or 0.0)
        # Executed book is long-only: a profitable close = the buy call was right.
        was_correct = pnl > 0

        latest = await self._memory.latest_by_ticker([entry.symbol])
        mem = latest.get(entry.symbol.upper())
        scores = dict(mem.scores) if mem and isinstance(mem.scores, dict) else {}
        rec_label = (mem.recommendation if mem else "buy") or "buy"

        members: list[dict[str, Any]] = []
        right: list[str] = []
        wrong: list[str] = []
        for agent_name, raw in scores.items():
            if agent_name in ("investment_director",):
                continue
            try:
                score = float(raw)
            except (TypeError, ValueError):
                continue
            if abs(score) < _SCORE_THRESHOLD:
                continue
            agent_right = (score > 0 and was_correct) or (score < 0 and not was_correct)
            label = agent_display_name(agent_name).title()
            members.append(
                {
                    "agent": agent_name,
                    "label_es": label,
                    "score": round(score, 1),
                    "right": agent_right,
                }
            )
            (right if agent_right else wrong).append(label)
        members.sort(key=lambda m: (not m["right"], -abs(float(m["score"]))))

        if mem is not None:
            tag = classify_error(rec_label, was_correct)
            exit_px = entry.exit_price or 0
            notes = (
                f"Cierre {entry.symbol}: ${entry.entry_price:.4g} → ${exit_px:.4g} "
                f"({pnl:+.2f}%). {entry.exit_reason or ''}".strip()
            )
            await self._memory.evaluate(
                mem.id, was_correct, notes, pnl, error_tag=tag
            )
            mem.was_correct = was_correct
            mem.actual_return_pct = pnl
            mem.evaluation_notes = notes
            mem.error_tag = tag
            await recalibrate_agent_weights(self._memory, scores, was_correct)
            if not was_correct:
                await self._learn.ingest_evaluation(mem, False)
                await self._learn.snapshot()

        review: dict[str, Any] = {
            "symbol": entry.symbol.upper(),
            "pnl_pct": round(pnl, 2),
            "pnl_usd": round(float(entry.pnl_usd), 4) if entry.pnl_usd is not None else None,
            "was_correct": was_correct,
            "recommendation": rec_label,
            "exit_reason": entry.exit_reason,
            "closed_at": entry.closed_at.isoformat() if entry.closed_at else None,
            "members": members,
            "right": right,
            "wrong": wrong,
            "journal_id": entry.id,
        }
        meta = dict(entry.meta or {})
        meta["member_review"] = review
        await self._journal.save_meta(entry.id, meta)
        await self._flags.set_json(FLAG_KEY, review)
        logger.info(
            "trade_close.member_review",
            symbol=review["symbol"],
            pnl_pct=review["pnl_pct"],
            correct=was_correct,
            right=right,
            wrong=wrong,
        )
        return review
