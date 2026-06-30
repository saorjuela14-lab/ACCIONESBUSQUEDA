"""Structured report models shared across agents and the investment director."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from domain.enums import (
    EvidenceCategory,
    ImpactLevel,
    InvestmentRecommendation,
    MarketSession,
    NewsSentiment,
    ReportType,
    StrategyType,
    TimeHorizon,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Reference(BaseModel):
    source: str
    url: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    data_point: str | None = None
    value: str | float | int | None = None


class Finding(BaseModel):
    category: EvidenceCategory
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    references: list[Reference] = Field(default_factory=list)
    impact: ImpactLevel | None = None
    horizon: TimeHorizon | None = None


class AgentReport(BaseModel):
    """Standard structured output for every analysis agent."""

    agent_name: str
    ticker: str | None = None
    score: float = Field(ge=-100.0, le=100.0, description="Directional score")
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    opportunities: list[Finding] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    summary: str


class ScenarioCase(BaseModel):
    name: str
    probability: float = Field(ge=0.0, le=1.0)
    price_target: float | None = None
    thesis: str
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class StrategyConclusion(BaseModel):
    strategy: StrategyType
    score: float = Field(ge=-100.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    conclusion: str
    horizon: TimeHorizon


class InvestmentThesis(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ticker: str
    executive_summary: str
    investment_thesis: str
    bull_case: ScenarioCase
    bear_case: ScenarioCase
    base_case: ScenarioCase
    catalysts: list[Finding] = Field(default_factory=list)
    risks: list[Finding] = Field(default_factory=list)
    recommendation: InvestmentRecommendation
    confidence: float = Field(ge=0.0, le=1.0)
    price_target: float | None = None
    agent_reports: list[AgentReport] = Field(default_factory=list)
    strategy_conclusions: list[StrategyConclusion] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


class MarketReport(BaseModel):
    report_type: ReportType
    session: MarketSession
    market_summary: str
    strong_sectors: list[str] = Field(default_factory=list)
    weak_sectors: list[str] = Field(default_factory=list)
    highest_volume: list[str] = Field(default_factory=list)
    highest_volatility: list[str] = Field(default_factory=list)
    news_highlights: list[Finding] = Field(default_factory=list)
    macro_events: list[Finding] = Field(default_factory=list)
    technical_changes: list[Finding] = Field(default_factory=list)
    fundamental_changes: list[Finding] = Field(default_factory=list)
    sentiment_changes: list[Finding] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


class DailyInvestmentReport(BaseModel):
    date: datetime = Field(default_factory=utc_now)
    market_report: MarketReport
    top_opportunities: list[str] = Field(default_factory=list)
    worst_performers: list[str] = Field(default_factory=list)
    watchlist_changes: list[str] = Field(default_factory=list)
    institutional_movements: list[Finding] = Field(default_factory=list)
    updated_recommendations: list[InvestmentThesis] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    title: str
    source: str
    url: str | None = None
    published_at: datetime | None = None
    sentiment: NewsSentiment = NewsSentiment.NEUTRAL
    impact: ImpactLevel = ImpactLevel.MEDIUM
    horizon: TimeHorizon = TimeHorizon.WEEKLY
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    related_tickers: list[str] = Field(default_factory=list)
