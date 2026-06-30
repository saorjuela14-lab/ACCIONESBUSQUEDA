"""Market dependency and correlation models."""

from pydantic import BaseModel, Field


class CorrelationPair(BaseModel):
    ticker: str
    correlation: float = Field(ge=-1.0, le=1.0)
    relationship: str
    interpretation: str


class MacroSensitivity(BaseModel):
    factor: str
    proxy_ticker: str
    correlation: float | None = None
    sensitivity: str  # high | medium | low
    scenario: str
    impact_if_shock: str


class CompanyDependency(BaseModel):
    ticker: str
    company_name: str | None = None
    relationship: str
    correlation: float | None = None
    why_it_matters: str


class MarketDependencyReport(BaseModel):
    ticker: str
    sector: str | None = None
    industry: str | None = None
    benchmark_correlations: list[CorrelationPair] = Field(default_factory=list)
    macro_sensitivities: list[MacroSensitivity] = Field(default_factory=list)
    company_dependencies: list[CompanyDependency] = Field(default_factory=list)
    emerging_market_exposure: str = ""
    summary: str = ""
    risk_score: float = 0.0
