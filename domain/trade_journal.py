"""Durable trade journal + track-record models for Monarch Capital desk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


JournalStatus = Literal["open", "closed"]


class TradeJournalEntry(BaseModel):
    id: str = ""
    symbol: str
    status: JournalStatus = "open"
    qty: float = 0.0
    entry_price: float = 0.0
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    r_multiple: float | None = None
    thesis: str | None = None
    source_tag: str | None = None
    exit_reason: str | None = None
    mandate_id: str | None = None
    opened_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class TrackRecordSummary(BaseModel):
    """Product track record — closed trades + evaluated thesis memory."""

    as_of: datetime = Field(default_factory=utc_now)
    window_days: int = 90
    trades_closed: int = 0
    trades_wins: int = 0
    trades_losses: int = 0
    trades_win_rate_pct: float | None = None
    trades_avg_pnl_pct: float | None = None
    trades_expectancy_pct: float | None = None
    trades_total_pnl_usd: float | None = None
    memory_evaluated: int = 0
    memory_correct: int = 0
    memory_hit_rate_pct: float | None = None
    open_positions: int = 0
    recent_closed: list[TradeJournalEntry] = Field(default_factory=list)
    disclaimer: str = (
        "Win rate sobre trades cerrados del journal de la mesa. "
        "N pequeño ≠ edge estable. No es predicción ni garantía de resultados futuros."
    )
    durable_db: bool = False
