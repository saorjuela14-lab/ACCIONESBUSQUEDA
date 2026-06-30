"""Investment proposal models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class InstrumentType(StrEnum):
    STOCK = "stock"
    CFD = "cfd"
    AUTO = "auto"


class RiskProfile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class AllocationLine(BaseModel):
    ticker: str
    company_name: str | None = None
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    allocation_usd: float
    allocation_pct: float
    instrument: InstrumentType
    price: float
    notional_exposure: float
    units: float
    margin_required: float | None = None
    margin_pct: float | None = None
    spread_cost_est: float | None = None
    overnight_financing_est: float | None = None
    stop_loss_suggested: float | None = None
    max_loss_est: float | None = None
    expected_return_pct: float | None = None
    horizon: str = "medium_term"
    purchase_order: int = 0
    rationale: str


class ExecutiveInvestmentReport(BaseModel):
    why_selected: list[str] = Field(default_factory=list)
    why_excluded: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    events_to_monitor: list[str] = Field(default_factory=list)
    correlation_notes: list[str] = Field(default_factory=list)
    invalidation_scenarios: list[str] = Field(default_factory=list)
    expected_return_pct: float | None = None
    max_loss_est_pct: float | None = None
    portfolio_risk_score: float | None = None
    cfd_rationale: list[str] = Field(default_factory=list)
    narrative: str = ""


class InvestmentProposal(BaseModel):
    budget: float
    risk_profile: RiskProfile
    instrument_mode: InstrumentType
    default_cfd_margin_pct: float
    cash_reserve_pct: float
    allocations: list[AllocationLine] = Field(default_factory=list)
    unallocated_cash: float = 0.0
    total_margin_required: float | None = None
    total_spread_cost: float | None = None
    total_overnight_est: float | None = None
    portfolio_expected_return_pct: float | None = None
    portfolio_max_loss_pct: float | None = None
    diversification_score: float | None = None
    instrument_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""
    executive_report: ExecutiveInvestmentReport | None = None
