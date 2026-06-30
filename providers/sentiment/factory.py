"""Factory for sentiment data providers."""

from providers.interfaces import SentimentProvider
from providers.sentiment.composite_sentiment_provider import CompositeSentimentProvider

_provider: SentimentProvider | None = None


def get_sentiment_provider() -> SentimentProvider:
    global _provider
    if _provider is None:
        _provider = CompositeSentimentProvider()
    return _provider


def reset_sentiment_provider() -> None:
    global _provider
    _provider = None
