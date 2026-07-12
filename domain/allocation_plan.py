"""Market-driven capital allocation plan models."""

from pydantic import BaseModel, Field


class TickerAllocationItem(BaseModel):
    ticker: str
    company_name: str | None = None
    bucket: str
    allocation_pct: float
    allocation_usd: float
    recommendation: str | None = None
    confidence: float | None = None
    score: float | None = None
    rationale: str = ""
    is_emerging: bool = False


class AllocationBucket(BaseModel):
    key: str
    label: str
    allocation_pct: float
    allocation_usd: float
    tickers: list[str] = Field(default_factory=list)
    description: str = ""


class MarketAllocationPlan(BaseModel):
    capital: float
    market_regime: str
    market_regime_score: float = 0.0
    strategy_style: str
    market_view: str
    summary: str
    cash_reserve_pct: float
    buckets: list[AllocationBucket] = Field(default_factory=list)
    items: list[TickerAllocationItem] = Field(default_factory=list)
    excluded_tickers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
