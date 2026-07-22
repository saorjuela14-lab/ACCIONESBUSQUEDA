"""Operational autonomy models — audit, kill switch, position lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


AuditAction = Literal[
    "buy_submit",
    "sell_submit",
    "close_position",
    "close_all",
    "kill_switch_on",
    "kill_switch_off",
    "reconcile",
    "lifecycle_exit",
    "auto_execute",
    "risk_block",
    "trailing_update",
    "thesis_invalidate",
]


class AuditEvent(BaseModel):
    id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    action: str
    actor: str = "system"  # system | scheduler | user | kill_switch
    symbol: str | None = None
    paper: bool | None = None
    success: bool = True
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class KillSwitchState(BaseModel):
    active: bool = False
    reason: str | None = None
    activated_at: datetime | None = None
    activated_by: str | None = None
    flat_attempted: bool = False
    flat_result: str | None = None


class PositionMandate(BaseModel):
    """Per-position risk mandate tracked by the lifecycle desk."""

    id: str = ""
    symbol: str
    qty: float = 0.0
    entry_price: float = 0.0
    opened_at: datetime = Field(default_factory=utc_now)
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_pct: float | None = None  # e.g. 0.08 = 8% trail from peak
    peak_price: float | None = None
    time_stop_days: int | None = None
    thesis: str | None = None
    thesis_invalidated: bool = False
    invalidate_reason: str | None = None
    sector: str | None = None
    beta: float | None = None
    status: str = "open"  # open | closed | exiting
    last_checked_at: datetime | None = None
    exit_reason: str | None = None
    closed_at: datetime | None = None


class LifecycleAction(BaseModel):
    symbol: str
    action: Literal["hold", "tighten_stop", "exit"] = "hold"
    reason: str = ""
    new_stop: float | None = None
    executed: bool = False
    detail: str | None = None


class LifecycleScanReport(BaseModel):
    scanned_at: datetime = Field(default_factory=utc_now)
    positions: int = 0
    actions: list[LifecycleAction] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReconcileDiff(BaseModel):
    symbol: str
    field: str
    alpaca: Any = None
    nexbuy: Any = None


class ReconcileReport(BaseModel):
    reconciled_at: datetime = Field(default_factory=utc_now)
    portfolio_id: str | None = None
    alpaca_positions: int = 0
    nexbuy_positions: int = 0
    cash_alpaca: float | None = None
    cash_nexbuy: float | None = None
    diffs: list[ReconcileDiff] = Field(default_factory=list)
    synced: bool = False
    message: str = ""


class PortfolioRiskMetrics(BaseModel):
    equity: float = 0.0
    var_1d_95_pct: float | None = None  # approximate historical VaR %
    var_1d_95_usd: float | None = None
    portfolio_beta: float | None = None
    sector_weights: dict[str, float] = Field(default_factory=dict)
    max_sector: str | None = None
    max_sector_pct: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class AutoExecutePolicy(BaseModel):
    enabled: bool = False
    paper_only_until_promoted: bool = True
    live_enabled: bool = False
    max_notional: float = 25.0
    require_market_open: bool = True
    require_risk_desk_ok: bool = True
    promotion_note: str = (
        "Paper primero: AUTO_EXECUTE_TRADES=true con ALPACA_PAPER=true. "
        "LIVE solo con AUTO_EXECUTE_LIVE=true y límites bajos."
    )
