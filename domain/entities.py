"""Domain entities for portfolios, watchlists, and alerts."""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from domain.enums import AlertSeverity, AlertType, StrategyType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ticker: str
    company_name: str | None = None
    added_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None
    active: bool = True


class PortfolioPosition(BaseModel):
    ticker: str
    shares: float
    average_cost: float
    current_price: float | None = None

    @property
    def market_value(self) -> float | None:
        if self.current_price is None:
            return None
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.shares * self.average_cost


class Portfolio(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    strategy: StrategyType
    initial_capital: float
    cash: float
    positions: list[PortfolioPosition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def invested_value(self) -> float:
        return sum(p.market_value or p.cost_basis for p in self.positions)

    @property
    def total_value(self) -> float:
        return self.cash + self.invested_value

    @property
    def return_pct(self) -> float:
        if self.initial_capital <= 0:
            return 0.0
        return ((self.total_value - self.initial_capital) / self.initial_capital) * 100


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ticker: str
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    description: str
    created_at: datetime = Field(default_factory=utc_now)
    acknowledged: bool = False


class StrategyProfile(BaseModel):
    strategy: StrategyType
    enabled: bool = True
    weight: float = Field(default=1.0, ge=0.0, le=10.0)
    description: str = ""


class InvestmentMemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ticker: str
    thesis: str
    reasons: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    scenario: str
    expected_outcome: str
    recommendation: str
    entry_price: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
    evaluated_at: datetime | None = None
    was_correct: bool | None = None
    evaluation_notes: str | None = None
    actual_return_pct: float | None = None
