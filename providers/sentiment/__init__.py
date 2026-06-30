"""Social sentiment providers."""

from providers.sentiment.composite_sentiment_provider import CompositeSentimentProvider
from providers.sentiment.factory import get_sentiment_provider, reset_sentiment_provider
from providers.sentiment.reddit_search_provider import RedditSearchProvider
from providers.sentiment.stocktwits_provider import StocktwitsProvider
from providers.sentiment.web_sentiment_provider import WebSentimentProvider

__all__ = [
    "CompositeSentimentProvider",
    "RedditSearchProvider",
    "StocktwitsProvider",
    "WebSentimentProvider",
    "get_sentiment_provider",
    "reset_sentiment_provider",
]
