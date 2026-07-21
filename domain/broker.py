"""Alpaca / broker trading domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]


class BrokerAccount(BaseModel):
    id: str = ""
    status: str = ""
    currency: str = "USD"
    cash: float = 0.0
    buying_power: float = 0.0
    portfolio_value: float = 0.0
    equity: float = 0.0
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    account_blocked: bool = False
    paper: bool = True
    raw: dict[str, Any] = Field(default_factory=dict)


class BrokerPosition(BaseModel):
    symbol: str
    qty: float
    side: str = "long"
    market_value: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_plpc: float = 0.0
    asset_class: str = "us_equity"


class BrokerOrderRequest(BaseModel):
    symbol: str
    qty: float = Field(gt=0)
    side: OrderSide = "buy"
    order_type: OrderType = "market"
    time_in_force: TimeInForce = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    client_order_id: str | None = None
    extended_hours: bool = False


class BrokerOrderResult(BaseModel):
    id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    qty: float = 0.0
    filled_qty: float = 0.0
    side: str = ""
    type: str = ""
    status: str = ""
    submitted_at: datetime | None = None
    filled_avg_price: float | None = None
    request_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BrokerStatus(BaseModel):
    configured: bool = False
    paper: bool = True
    connected: bool = False
    message: str = ""
    account: BrokerAccount | None = None
    base_url: str = ""
    last_request_id: str | None = None


class ExecuteLine(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    side: OrderSide = "buy"
    order_type: OrderType = "market"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


class ExecuteOrdersRequest(BaseModel):
    lines: list[ExecuteLine] = Field(min_length=1)
    dry_run: bool = False
    confirm_live: bool = Field(
        default=False,
        description="Obligatorio True si ALPACA_PAPER=false (cuenta real)",
    )
    sync_portfolio_id: str | None = Field(
        default=None,
        description="Si se indica, refleja fills en el portafolio interno NexBuy",
    )


class ExecuteOrdersResponse(BaseModel):
    paper: bool = True
    dry_run: bool = False
    submitted: list[BrokerOrderResult] = Field(default_factory=list)
    failed: list[BrokerOrderResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
