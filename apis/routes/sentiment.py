"""Sentiment API — multi-channel engine."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import get_session
from database.repositories.sentiment_history_repository import SentimentHistoryRepository
from domain.sentiment import SentimentSnapshot
from domain.sentiment_v2 import SentimentEngineReport, SentimentHistoryPoint
from providers.market.factory import get_market_provider
from providers.sentiment.factory import get_sentiment_provider
from services.sentiment_engine_service import SentimentEngineService

router = APIRouter()


@router.get("/sentiment/{ticker}", response_model=SentimentSnapshot)
async def get_sentiment_legacy(ticker: str) -> SentimentSnapshot:
    provider = get_sentiment_provider()
    return await provider.get_sentiment(ticker.upper())


async def _run_engine(ticker: str, session: AsyncSession | None = None) -> SentimentEngineReport:
    quote = await get_market_provider().get_quote(ticker.upper())
    engine = SentimentEngineService()
    report = await engine.analyze(ticker.upper(), quote.get("company_name"))
    if session:
        await SentimentHistoryRepository(session).save(
            ticker=report.ticker,
            aggregated_score=report.aggregated_score,
            label=report.aggregated_label,
            retail_score=report.retail.score,
            news_score=report.news.score,
            institutional_score=report.institutional.score,
        )
    return report


@router.get("/sentiment/{ticker}/engine", response_model=SentimentEngineReport)
async def get_sentiment_engine(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> SentimentEngineReport:
    return await _run_engine(ticker, session)


@router.get("/sentiment/{ticker}/history", response_model=list[SentimentHistoryPoint])
async def get_sentiment_history(
    ticker: str,
    limit: int = 90,
    session: AsyncSession = Depends(get_session),
) -> list[SentimentHistoryPoint]:
    return await SentimentHistoryRepository(session).list_for_ticker(ticker.upper(), limit=limit)
