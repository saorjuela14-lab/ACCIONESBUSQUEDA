"""Demo portfolio simulation and projection models."""

from pydantic import BaseModel, Field


class ProjectionPoint(BaseModel):
    month: int
    label: str
    pessimistic: float
    base: float
    optimistic: float


class ScenarioOutcome(BaseModel):
    name: str
    horizon_months: int
    projected_value: float
    return_pct: float
    description: str


class PortfolioProjectionReport(BaseModel):
    portfolio_id: str
    mode: str
    current_value: float
    initial_capital: float
    horizon_months: int
    annual_return_pct: float
    annual_volatility_pct: float
    points: list[ProjectionPoint] = Field(default_factory=list)
    scenarios: list[ScenarioOutcome] = Field(default_factory=list)
    summary: str = ""
