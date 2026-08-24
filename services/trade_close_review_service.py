"""Grade committee members by operation outcome (stop vs TP), not P&L dollars."""

from __future__ import annotations

from typing import Any, Literal

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

OperationOutcome = Literal["win", "loss", "gestion"]

_WIN_HINTS = ("take-profit", "take profit", "take_profit")
_LOSS_HINTS = (
    "stop/trailing",
    "stop tocado",
    "trailing tocado",
    "tesis invalid",
    "tesis_invalidada",
    "time-stop",
    "time_stop",
)
_GESTION_HINTS = (
    "eod",
    "smart flat",
    "asegurar_ganancia",
    "asegurar ganancia",
    "posición ausente",
    "posicion ausente",
    "ausente en alpaca",
)


def classify_operation(entry: TradeJournalEntry) -> tuple[OperationOutcome, str]:
    """2R desk: the operation is TP (win), stop/tesis (loss), or process close (no verdict)."""
    reason = (entry.exit_reason or "").lower()
    exit_px = entry.exit_price
    stop = entry.stop_loss
    tp = entry.take_profit

    if any(h in reason for h in _WIN_HINTS):
        return "win", "take_profit"
    if any(h in reason for h in _LOSS_HINTS) or (
        "stop" in reason and "eod" not in reason and "flat" not in reason
    ):
        return "loss", "stop" if "tesis" not in reason else "thesis_invalidated"
    if any(h in reason for h in _GESTION_HINTS):
        return "gestion", "gestion"

    if tp is not None and exit_px is not None and float(tp) > 0:
        if float(exit_px) >= float(tp) * 0.995:
            return "win", "take_profit"
    if stop is not None and exit_px is not None and float(stop) > 0:
        if float(exit_px) <= float(stop) * 1.005:
            return "loss", "stop"
    return "gestion", "gestion"


class TradeCloseReviewService:
    """Scorecard by operation (TP/stop/tesis), never by dollar P&L."""

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
            if entry.status != "closed":
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

        outcome, outcome_tag = classify_operation(entry)
        verdict = outcome != "gestion"
        was_correct = True if outcome == "win" else False if outcome == "loss" else None

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
            label = agent_display_name(agent_name).title()
            stance = "a_favor" if score > 0 else "en_contra"
            agent_right: bool | None = None
            if was_correct is not None:
                agent_right = (score > 0 and was_correct) or (score < 0 and not was_correct)
            members.append(
                {
                    "agent": agent_name,
                    "label_es": label,
                    "score": round(score, 1),
                    "stance": stance,
                    "right": agent_right,
                }
            )
            if agent_right is True:
                right.append(label)
            elif agent_right is False:
                wrong.append(label)
        members.sort(
            key=lambda m: (
                m["right"] is not True,
                -abs(float(m["score"])),
            )
        )

        if mem is not None and was_correct is not None:
            tag = classify_error(rec_label, was_correct)
            notes = (
                f"Operación {entry.symbol}: {outcome_tag}. "
                f"{entry.exit_reason or ''}".strip()
            )
            await self._memory.evaluate(
                mem.id,
                was_correct,
                notes,
                float(entry.pnl_pct or 0.0),
                error_tag=tag,
            )
            mem.was_correct = was_correct
            mem.actual_return_pct = entry.pnl_pct
            mem.evaluation_notes = notes
            mem.error_tag = tag
            await recalibrate_agent_weights(self._memory, scores, was_correct)
            if not was_correct:
                await self._learn.ingest_evaluation(mem, False)
                await self._learn.snapshot()

        review: dict[str, Any] = {
            "symbol": entry.symbol.upper(),
            "outcome": outcome,
            "outcome_tag": outcome_tag,
            "verdict": verdict,
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
            outcome=outcome,
            correct=was_correct,
            right=right,
            wrong=wrong,
        )
        return review
