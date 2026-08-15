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
