"""Correlation and market dependency API routes."""

from fastapi import APIRouter

from domain.correlations import MarketDependencyReport
from providers.market.factory import get_market_provider
from services.correlation_service import CorrelationService

router = APIRouter()


@router.get("/correlations/{ticker}", response_model=MarketDependencyReport)
async def get_correlations(ticker: str) -> MarketDependencyReport:
    service = CorrelationService(get_market_provider())
    return await service.analyze(ticker.upper())
