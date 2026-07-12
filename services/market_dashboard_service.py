"""Terminal dashboard aggregation service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import yfinance as yf

from domain.dashboard import (
    EconomicEvent,
    IndexQuote,
    NewsHighlight,
    PortfolioDashboardSlice,
    SectorHeatmapItem,
    TerminalDashboard,
    TickerOpportunity,
)
from providers.macro.factory import get_macro_provider
from services.sentiment_engine_service import SentimentEngineService
from utils.logging import get_logger

logger = get_logger(__name__)

INDICES = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("DIA", "Dow Jones"),
    ("IWM", "Russell 2000"),
    ("^VIX", "VIX"),
    ("UUP", "US Dollar Index"),
]

SECTOR_ETFS = [
    ("XLK", "Technology"),
    ("XLV", "Healthcare"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLI", "Industrials"),
    ("XLY", "Consumer Disc."),
    ("XLP", "Consumer Staples"),
    ("XLU", "Utilities"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
    ("XLC", "Communication"),
]


class MarketDashboardService:
    def __init__(self) -> None:
        self._sentiment = SentimentEngineService()

    def _fetch_quote_sync(self, symbol: str, name: str) -> IndexQuote:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            if hist.empty:
                return IndexQuote(symbol=symbol, name=name)
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
            chg = ((price - prev) / prev * 100) if prev else 0
            return IndexQuote(symbol=symbol, name=name, price=round(price, 2), change_pct=round(chg, 2))
        except Exception:
            return IndexQuote(symbol=symbol, name=name)

    async def _fetch_indices(self) -> list[IndexQuote]:
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self._fetch_quote_sync, sym, name) for sym, name in INDICES]
        return list(await asyncio.gather(*tasks))

    async def _fetch_sector_heatmap(self) -> list[SectorHeatmapItem]:
        loop = asyncio.get_event_loop()

        def _one(etf: str, sector: str) -> SectorHeatmapItem:
            try:
                hist = yf.Ticker(etf).history(period="5d")
                if hist.empty:
                    return SectorHeatmapItem(sector=sector, etf=etf)
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
                chg = ((price - prev) / prev * 100) if prev else 0
                regime = "bullish" if chg > 0.5 else "bearish" if chg < -0.5 else "neutral"
                return SectorHeatmapItem(sector=sector, etf=etf, change_pct=round(chg, 2), regime=regime)
            except Exception:
                return SectorHeatmapItem(sector=sector, etf=etf)

        tasks = [loop.run_in_executor(None, _one, etf, sec) for etf, sec in SECTOR_ETFS]
        return list(await asyncio.gather(*tasks))

    def _compute_market_regime(self, indices: list[IndexQuote], sectors: list[SectorHeatmapItem]) -> tuple[str, float]:
        changes = [i.change_pct for i in indices if i.change_pct is not None and i.symbol != "^VIX"]
        vix = next((i for i in indices if i.symbol == "^VIX"), None)
        avg = sum(changes) / len(changes) if changes else 0
        bull_sectors = sum(1 for s in sectors if s.regime == "bullish")
        score = avg + (bull_sectors - len(sectors) / 2) * 0.1
        if vix and vix.change_pct and vix.change_pct > 5:
            score -= 1.0
        if score > 0.3:
            return "bullish", round(score, 2)
        if score < -0.3:
            return "bearish", round(score, 2)
        return "neutral", round(score, 2)

    async def _economic_calendar(self) -> list[EconomicEvent]:
        try:
            macro = get_macro_provider()
            raw = await macro.get_economic_calendar(days=7)
            return [
                EconomicEvent(
                    title=e.get("title", e.get("event", "Macro event")),
                    date=str(e.get("date", "")),
                    importance=e.get("importance", "medium"),
                    category=e.get("category", "macro"),
                )
                for e in raw[:12]
            ]
        except Exception as exc:
            logger.warning("dashboard.calendar.failed", error=str(exc))
            return []

    @staticmethod
    def _parse_yf_news_entry(entry: dict) -> NewsHighlight | None:
        c = entry.get("content") or entry
        title = c.get("title", "")
        if not title:
            return None
        provider = c.get("provider")
        source = provider.get("displayName", "Yahoo") if isinstance(provider, dict) else "Yahoo"
        canonical = c.get("canonicalUrl")
        url = canonical.get("url") if isinstance(canonical, dict) else c.get("link")
        summary = (c.get("summary") or c.get("description") or "").strip() or None
        published_at = c.get("pubDate") or c.get("displayTime")
        thumbnail_url = None
        thumb = c.get("thumbnail")
        if isinstance(thumb, dict):
            thumbnail_url = thumb.get("originalUrl")
            if not thumbnail_url:
                resolutions = thumb.get("resolutions") or []
                if resolutions and isinstance(resolutions[0], dict):
                    thumbnail_url = resolutions[0].get("url")
        return NewsHighlight(
            title=title,
            source=source,
            url=url,
            summary=summary,
            published_at=published_at,
            thumbnail_url=thumbnail_url,
            sentiment="neutral",
        )

    async def _market_news(self) -> list[NewsHighlight]:
        highlights: list[NewsHighlight] = []
        seen_titles: set[str] = set()
        for sym in ("SPY", "QQQ", "IWM"):
            try:
                for entry in (yf.Ticker(sym).news or [])[:5]:
                    item = self._parse_yf_news_entry(entry)
                    if not item or item.title in seen_titles:
                        continue
                    seen_titles.add(item.title)
                    highlights.append(item)
            except Exception:
                pass
        return highlights[:12]

    async def build(
        self,
        watchlist: list[str] | None = None,
        alerts: list[str] | None = None,
        portfolio_slice: PortfolioDashboardSlice | None = None,
        opportunities: list[TickerOpportunity] | None = None,
        risks: list[TickerOpportunity] | None = None,
        recently_analyzed: list[str] | None = None,
        provider_health: dict | None = None,
    ) -> TerminalDashboard:
        indices, sectors, calendar, news = await asyncio.gather(
            self._fetch_indices(),
            self._fetch_sector_heatmap(),
            self._economic_calendar(),
            self._market_news(),
        )
        regime, regime_score = self._compute_market_regime(indices, sectors)

        # Market sentiment from SPY
        try:
            spy_sent = await self._sentiment.analyze("SPY")
            mkt_sent_score = spy_sent.aggregated_score
            mkt_sent_label = spy_sent.aggregated_label
        except Exception:
            mkt_sent_score = 0.0
            mkt_sent_label = "neutral"

        return TerminalDashboard(
            market_regime=regime,
            market_regime_score=regime_score,
            indices=indices,
            sector_heatmap=sectors,
            economic_calendar=calendar,
            market_sentiment_score=mkt_sent_score,
            market_sentiment_label=mkt_sent_label,
            news_highlights=news,
            active_alerts=alerts or [],
            watchlist=watchlist or [],
            top_opportunities=opportunities or [],
            top_risks=risks or [],
            recently_analyzed=recently_analyzed or [],
            portfolio=portfolio_slice,
            provider_health=provider_health or {},
            timestamp=datetime.now(timezone.utc),
        )
