"""Sentiment API — multi-channel engine."""

from fastapi import APIRouter

from domain.sentiment import SentimentSnapshot
from domain.sentiment_v2 import SentimentEngineReport
from providers.market.factory import get_market_provider
from providers.sentiment.factory import get_sentiment_provider
from services.sentiment_engine_service import SentimentEngineService

router = APIRouter()


@router.get("/sentiment/{ticker}", response_model=SentimentSnapshot)
async def get_sentiment_legacy(ticker: str) -> SentimentSnapshot:
    provider = get_sentiment_provider()
    return await provider.get_sentiment(ticker.upper())


@router.get("/sentiment/{ticker}/engine", response_model=SentimentEngineReport)
async def get_sentiment_engine(ticker: str) -> SentimentEngineReport:
    quote = await get_market_provider().get_quote(ticker.upper())
    engine = SentimentEngineService()
    return await engine.analyze(ticker.upper(), quote.get("company_name"))
