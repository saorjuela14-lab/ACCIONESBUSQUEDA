"""Market chart data API."""

from fastapi import APIRouter, Query

from domain.dashboard import PriceChartData, PriceChartPoint, TechnicalChartData
from providers.market.factory import get_market_provider
from services.technical_chart_service import TechnicalChartService

router = APIRouter()


@router.get("/market/{ticker}/chart", response_model=PriceChartData)
async def get_price_chart(
    ticker: str,
    period: str = Query(default="6mo", pattern=r"^(1mo|3mo|6mo|1y|2y|5y)$"),
) -> PriceChartData:
    df = await get_market_provider().get_history(ticker.upper(), period=period, interval="1d")
    points: list[PriceChartPoint] = []
    if not df.empty:
        for idx, row in df.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            vol = float(row["Volume"]) if "Volume" in row and row["Volume"] == row["Volume"] else None
            points.append(
                PriceChartPoint(
                    date=date_str,
                    open=round(float(row["Open"]), 4) if "Open" in row else None,
                    high=round(float(row["High"]), 4) if "High" in row else None,
                    low=round(float(row["Low"]), 4) if "Low" in row else None,
                    close=round(float(row["Close"]), 4),
                    volume=vol,
                )
            )
    return PriceChartData(ticker=ticker.upper(), period=period, points=points)


@router.get("/market/{ticker}/technical", response_model=TechnicalChartData)
async def get_technical_chart(
    ticker: str,
    period: str = Query(default="6mo", pattern=r"^(1mo|3mo|6mo|1y|2y|5y)$"),
) -> TechnicalChartData:
    """OHLC velas + indicadores técnicos (RSI, MACD, SMA, Bollinger)."""
    return await TechnicalChartService(get_market_provider()).build(ticker.upper(), period=period)
