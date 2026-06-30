"""Advanced sentiment models — multi-channel institutional terminal."""

from datetime import datetime

from pydantic import BaseModel, Field


class SentimentChannel(BaseModel):
    name: str
    score: float = Field(ge=-100.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    trend: str = "stable"  # rising | falling | stable
    sample_size: int = 0
    top_factors: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


class SentimentEngineReport(BaseModel):
    ticker: str
    company_name: str | None = None
    aggregated_score: float = Field(ge=-100.0, le=100.0)
    aggregated_label: str = "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    institutional: SentimentChannel
    retail: SentimentChannel
    social: SentimentChannel
    news: SentimentChannel
    analyst: SentimentChannel
    sources_used: list[str] = Field(default_factory=list)
    sources_failed: list[str] = Field(default_factory=list)
    summary: str = ""
    timestamp: datetime | None = None


class SentimentHistoryPoint(BaseModel):
    timestamp: datetime
    aggregated_score: float
    label: str = "neutral"
    retail_score: float = 0.0
    news_score: float = 0.0
    institutional_score: float = 0.0
