"""Micro capital portfolio management models."""

from pydantic import BaseModel, Field

from domain.daily_trade import TradePick


class MicroAllocationLineOut(BaseModel):
    ticker: str
    company_name: str | None = None
    price: float
    shares: int
    allocation_usd: float
    allocation_pct: float
    rationale: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None


class MicroPortfolioPlanOut(BaseModel):
    capital: float
    cash_reserve_usd: float
    deployable_usd: float
    max_share_price: float
    lines: list[MicroAllocationLineOut] = Field(default_factory=list)
    picks: list[TradePick] = Field(default_factory=list)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
