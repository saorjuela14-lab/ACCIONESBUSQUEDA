"""API request/response schemas."""

from pydantic import BaseModel, Field

from domain.enums import StrategyType
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
