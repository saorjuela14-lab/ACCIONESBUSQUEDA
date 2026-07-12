"""Company discovery models — social media + news pipeline."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from domain.reports import InvestmentThesis


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiscoveryMention(BaseModel):
    source: str
    text: str
    url: str | None = None
    sentiment: str | None = None
    author: str | None = None
    published_at: datetime | None = None


class DiscoveryCandidate(BaseModel):
    ticker: str
    company_name: str | None = None
    score: float = 0.0
    mention_count: int = 0
    sources: list[str] = Field(default_factory=list)
    sentiment_score: float | None = None
    news_headlines: list[str] = Field(default_factory=list)
    mentions: list[DiscoveryMention] = Field(default_factory=list)
    rationale: str = ""


class DiscoveryReport(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    query_themes: list[str] = Field(default_factory=list)
    candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    summary: str = ""
    sources_scanned: list[str] = Field(default_factory=list)
    total_mentions_found: int = 0


class DiscoveryAnalyzeResult(BaseModel):
    discovery: DiscoveryReport
    analyses: list[InvestmentThesis] = Field(default_factory=list)
    recommendation_summary: str = ""
