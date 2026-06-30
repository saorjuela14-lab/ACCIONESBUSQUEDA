"""API request/response schemas."""

from pydantic import BaseModel, Field

from domain.enums import StrategyType
from domain.proposal import InvestmentProposal
from domain.reports import InvestmentThesis


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    portfolio_id: str | None = None


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    notes: str | None = None


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    strategy: StrategyType = StrategyType.GROWTH
    initial_capital: float = Field(gt=0)
    cash: float | None = None


class PositionAddRequest(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    average_cost: float = Field(gt=0)


class ThesisResponse(BaseModel):
    thesis: InvestmentThesis


class InvestmentProposalRequest(BaseModel):
    budget: float = Field(gt=0, description="Total capital to allocate (e.g. 50)")
    tickers: list[str] | None = Field(default=None, description="Tickers to consider")
    use_watchlist: bool = Field(default=False, description="Use active watchlist if tickers empty")
    instrument_mode: str = Field(default="auto", description="auto | stock | cfd")
    risk_profile: str = Field(default="balanced", description="conservative | balanced | aggressive")
    cfd_margin_pct: float | None = Field(default=None, ge=5, le=50, description="CFD margin % (default by risk profile)")
    use_llm_narrative: bool = Field(default=True, description="Enrich with LLM if OPENAI_API_KEY set")


class ProposalApplyRequest(BaseModel):
    portfolio_id: str
    proposal: InvestmentProposal
