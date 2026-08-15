"""Multi-asset beta desks — gold, forex proxies, crypto (Alpaca paper)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

AssetDeskId = Literal["gold", "forex", "crypto"]
OrderSide = Literal["buy", "sell"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeskUniverseItem(BaseModel):
    symbol: str
    label: str
    asset_class: str
    notes: str = ""


class DeskStrategy(BaseModel):
    desk: AssetDeskId
    name: str
    thesis: str
    horizon: str
    max_notional_usd: float = 250.0
    default_stop_pct: float = 0.05
    default_target_pct: float = 0.10
    allow_fractional: bool = False
    time_in_force: str = "day"  # crypto uses gtc
    symbols: list[DeskUniverseItem] = Field(default_factory=list)
    agent_names: list[str] = Field(default_factory=list)
    beta: bool = True
    disclaimer: str = ""


class AgentVote(BaseModel):
    agent_name: str
    label_es: str
    score: float
    confidence: float
    summary: str
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)


class DeskBrief(BaseModel):
    desk: AssetDeskId
    symbol: str
    generated_at: datetime = Field(default_factory=utc_now)
    recommendation: str  # buy | hold | sell
    confidence: float
    score: float
    summary: str
    entry_hint: float | None = None
    stop_hint: float | None = None
    target_hint: float | None = None
    votes: list[AgentVote] = Field(default_factory=list)
    strategy: DeskStrategy | None = None


class MultiAssetOrderRequest(BaseModel):
    desk: AssetDeskId
    symbol: str
    side: OrderSide
    qty: float | None = None
    notional: float | None = None
    dry_run: bool = False
    confirm: bool = False
    note: str = ""


class MultiAssetOrderResult(BaseModel):
    ok: bool
    desk: AssetDeskId
    symbol: str
    side: OrderSide
    paper: bool = True
    dry_run: bool = False
    order_id: str | None = None
    status: str | None = None
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class MultiAssetJournalEntry(BaseModel):
    id: str = ""
    desk: AssetDeskId
    symbol: str
    side: str
    qty: float | None = None
    notional: float | None = None
    order_id: str | None = None
    status: str | None = None
    note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    raw: dict[str, Any] = Field(default_factory=dict)


class DeskStatus(BaseModel):
    desk: AssetDeskId
    strategy: DeskStrategy
    paper: bool = True
    broker_configured: bool = False
    broker_message: str = ""
    equity: float | None = None
    cash: float | None = None
    positions: list[dict[str, Any]] = Field(default_factory=list)
    open_orders: list[dict[str, Any]] = Field(default_factory=list)
    quotes: dict[str, Any] = Field(default_factory=dict)


TradeStatus = Literal["open", "closed"]


class MultiAssetTrade(BaseModel):
    """Open→close paper trade with brief scores for effectiveness feedback."""

    id: str = ""
    desk: AssetDeskId
    symbol: str
    status: TradeStatus = "open"
    qty: float = 0.0
    entry_price: float = 0.0
    exit_price: float | None = None
    stop_hint: float | None = None
    target_hint: float | None = None
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    r_multiple: float | None = None
    recommendation: str = "hold"  # buy | hold | sell at open
    confidence: float | None = None
    score: float | None = None
    brief_summary: str = ""
    scores: dict[str, float] = Field(default_factory=dict)  # agent → score
    was_correct: bool | None = None
    error_tag: str | None = None  # false_long | false_short | missed_up | …
    eval_notes: str = ""
    is_sim: bool = False  # dry-run / sim paper
    order_id: str | None = None
    opened_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
    evaluated_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ErrorPattern(BaseModel):
    tag: str
    label_es: str
    count: int = 0
    share_pct: float | None = None
    hint_es: str = ""


class AgentDeskStat(BaseModel):
    agent_name: str
    label_es: str
    samples: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate_pct: float | None = None
    avg_score_when_right: float | None = None
    avg_score_when_wrong: float | None = None


class MultiAssetTrackRecord(BaseModel):
    desk: AssetDeskId | None = None
    as_of: datetime = Field(default_factory=utc_now)
    window_days: int = 90
    trades_closed: int = 0
    trades_wins: int = 0
    trades_losses: int = 0
    trades_win_rate_pct: float | None = None
    trades_avg_pnl_pct: float | None = None
    trades_total_pnl_usd: float | None = None
    briefs_evaluated: int = 0
    briefs_correct: int = 0
    brief_hit_rate_pct: float | None = None
    open_trades: int = 0
    pending_eval: int = 0
    recent_closed: list[MultiAssetTrade] = Field(default_factory=list)
    error_patterns: list[ErrorPattern] = Field(default_factory=list)
    agents: list[AgentDeskStat] = Field(default_factory=list)
    best_agent: str | None = None
    weakest_agent: str | None = None
    feedback: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Win rate y acierto de brief sobre trades cerrados/evaluados de la mesa beta. "
        "N pequeño ≠ edge estable. Sirve para bajar errores a futuro, no garantiza resultados."
    )
    durable_db: bool = False
