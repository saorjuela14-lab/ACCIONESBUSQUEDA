"""Shared AnalysisService construction for API routes and autonomous desks."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.repositories.alert_repository import AlertRepository
from database.repositories.investment_memory_repository import InvestmentMemoryRepository
from providers.macro.factory import get_macro_provider
from providers.market.factory import get_market_provider
from providers.news.factory import get_news_provider
from providers.sentiment.factory import get_sentiment_provider
from services.analysis_service import AnalysisService


def build_analysis_service(session: AsyncSession) -> AnalysisService:
    settings = get_settings()
    return AnalysisService(
        market_provider=get_market_provider(),
        news_provider=get_news_provider(),
        macro_provider=get_macro_provider(),
        alert_repo=AlertRepository(session),
        memory_repo=InvestmentMemoryRepository(session),
        sentiment_provider=get_sentiment_provider(),
        max_concentration_pct=settings.max_concentration_pct,
    )
