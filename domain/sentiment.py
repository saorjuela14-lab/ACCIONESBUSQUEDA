"""Social sentiment data models."""

from datetime import datetime

from pydantic import BaseModel, Field

from domain.enums import NewsSentiment


class SentimentItem(BaseModel):
    """Structured social sentiment post from any source."""

    source: str  # stocktwits | reddit | seeking_alpha | yahoo_finance
    text: str
    url: str | None = None
    sentiment: NewsSentiment = NewsSentiment.NEUTRAL
    author: str | None = None
    engagement: int | None = None
    published_at: datetime | None = None


class SentimentSnapshot(BaseModel):
    """Aggregated sentiment from multiple social sources."""

    ticker: str
    items: list[SentimentItem] = Field(default_factory=list)
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    sources: list[str] = Field(default_factory=list)
    stocktwits_bullish_pct: float | None = None
    score: float = 0.0
