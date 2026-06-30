"""News provider factory."""

from providers.news.composite_news_provider import CompositeNewsProvider


def get_news_provider() -> CompositeNewsProvider:
    return CompositeNewsProvider()
