"""CEO monthly desk report — honest 2R / stagnation metrics (not fake win rate)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SymbolPnlRow(BaseModel):
    symbol: str
    closes: int = 0
    pnl_usd: float = 0.0
    avg_pnl_pct: float | None = None


class OpenPositionRow(BaseModel):
    symbol: str
    qty: float = 0.0
    entry_price: float = 0.0
    pnl_pct: float | None = None
    opened_at: datetime | None = None


class MonthReport(BaseModel):
    """30-day CEO snapshot: equity, true 2R outcomes, agents, lessons."""

    as_of: datetime = Field(default_factory=utc_now)
    window_days: int = 30
    base_usd: float = 20.0
    equity_usd: float | None = None
    equity_return_pct: float | None = None
    closed_pnl_usd: float | None = None
    closed_avg_pnl_pct: float | None = None
    trades_closed: int = 0
    outcomes: dict[str, int] = Field(
        default_factory=lambda: {
            "win": 0,
            "loss": 0,
            "stagnation": 0,
            "gestion": 0,
            "unknown": 0,
        }
    )
    true_tp: int = 0
    true_stop: int = 0
    stagnation_pct: float | None = None
    # Legacy journal win rate (pnl>0) — shown with caveat
    journal_win_rate_pct: float | None = None
    thesis_hit_rate_pct: float | None = None
    theses_correct: int = 0
    theses_evaluated: int = 0
    spy_return_pct: float | None = None
    vs_spy_pct: float | None = None
    best_agent: str | None = None
    best_agent_label: str | None = None
    weakest_agent: str | None = None
    weakest_agent_label: str | None = None
    top_symbols: list[SymbolPnlRow] = Field(default_factory=list)
    open_positions: list[OpenPositionRow] = Field(default_factory=list)
    open_count: int = 0
    lessons_active: int = 0
    avoids: list[str] = Field(default_factory=list)
    headline: str = ""
    diagnosis: list[str] = Field(default_factory=list)
    durable_db: bool = False
    disclaimer: str = (
        "Informe del mes: TP/stop/estancamiento (sin avance 1.5%) son la verdad operativa. "
        "Win rate por P&L verde engaña cuando casi todo es EOD flat. "
        "N pequeño ≠ edge estable. Requiere DB persistente (Neon)."
    )
    meta: dict[str, Any] = Field(default_factory=dict)
