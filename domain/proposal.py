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
    rationale: str


class InvestmentProposal(BaseModel):
    budget: float
    risk_profile: RiskProfile
    instrument_mode: InstrumentType
    default_cfd_margin_pct: float
    cash_reserve_pct: float
    allocations: list[AllocationLine] = Field(default_factory=list)
    unallocated_cash: float = 0.0
    total_margin_required: float | None = None
    instrument_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""
