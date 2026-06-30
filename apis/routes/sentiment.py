"""Standalone sentiment API route."""

from fastapi import APIRouter

from domain.sentiment import SentimentSnapshot
from providers.sentiment.factory import get_sentiment_provider

router = APIRouter()


@router.get("/sentiment/{ticker}", response_model=SentimentSnapshot)
async def get_sentiment(ticker: str) -> SentimentSnapshot:
    provider = get_sentiment_provider()
    return await provider.get_sentiment(ticker.upper())
