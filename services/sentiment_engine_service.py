"""Multi-channel sentiment engine with automatic fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from domain.enums import NewsSentiment
from domain.sentiment import SentimentSnapshot
from domain.sentiment_v2 import SentimentChannel, SentimentEngineReport
from providers.sentiment.reddit_search_provider import RedditSearchProvider
from providers.sentiment.stocktwits_provider import StocktwitsProvider
from providers.sentiment.yfinance_news_sentiment_provider import YFinanceNewsSentimentProvider
from utils.logging import get_logger

logger = get_logger(__name__)

_ANALYST_KEYWORDS = ("upgrade", "downgrade", "outperform", "underperform", "price target", "analyst", "rating")
_INSTITUTIONAL_KEYWORDS = ("institutional", "hedge fund", "13f", "sec filing", "blackrock", "vanguard")


class SentimentEngineService:
    """Aggregates sentiment across channels with per-source fallback."""

    def __init__(self) -> None:
        self._stocktwits = StocktwitsProvider()
        self._reddit = RedditSearchProvider()
        self._news = YFinanceNewsSentimentProvider()

    def _channel_from_snapshot(
        self,
        name: str,
        snapshot: SentimentSnapshot | None,
        factors: list[str] | None = None,
    ) -> SentimentChannel:
        if not snapshot or not snapshot.items:
            return SentimentChannel(name=name, score=0.0, confidence=0.2, trend="stable", sample_size=0)

        total = snapshot.bullish_count + snapshot.bearish_count + snapshot.neutral_count
        conf = min(0.95, 0.35 + total * 0.03)
        trend = "rising" if snapshot.score > 10 else "falling" if snapshot.score < -10 else "stable"
        history = [
            {"text": item.text[:80], "sentiment": item.sentiment.value, "source": item.source}
            for item in snapshot.items[:5]
        ]
        return SentimentChannel(
            name=name,
            score=max(-100, min(100, snapshot.score)),
            confidence=conf,
            trend=trend,
            sample_size=total,
            top_factors=factors or [],
            history=history,
        )

    def _split_news_channels(self, news: SentimentSnapshot) -> tuple[SentimentChannel, SentimentChannel, SentimentChannel]:
        institutional_items = []
        analyst_items = []
        news_items = []
        for item in news.items:
            lower = item.text.lower()
            if any(k in lower for k in _INSTITUTIONAL_KEYWORDS):
                institutional_items.append(item)
            elif any(k in lower for k in _ANALYST_KEYWORDS):
                analyst_items.append(item)
            else:
                news_items.append(item)

        def _score_items(items: list) -> float:
            if not items:
                return 0.0
            bull = sum(1 for i in items if i.sentiment == NewsSentiment.BULLISH)
            bear = sum(1 for i in items if i.sentiment == NewsSentiment.BEARISH)
            return ((bull - bear) / len(items)) * 50

        inst = SentimentChannel(
            name="institutional",
            score=_score_items(institutional_items),
            confidence=0.5 if institutional_items else 0.2,
            trend="stable",
            sample_size=len(institutional_items),
            top_factors=[i.text[:60] for i in institutional_items[:3]],
        )
        analyst = SentimentChannel(
            name="analyst",
            score=_score_items(analyst_items),
            confidence=0.55 if analyst_items else 0.2,
            trend="stable",
            sample_size=len(analyst_items),
            top_factors=[i.text[:60] for i in analyst_items[:3]],
        )
        news_ch = SentimentChannel(
            name="news",
            score=_score_items(news_items) if news_items else news.score,
            confidence=0.6 if news_items else 0.3,
            trend="rising" if news.score > 5 else "falling" if news.score < -5 else "stable",
            sample_size=len(news_items) or len(news.items),
            top_factors=[i.text[:60] for i in (news_items or news.items)[:3]],
        )
        return inst, analyst, news_ch

    async def analyze(self, ticker: str, company_name: str | None = None) -> SentimentEngineReport:
        sources_used: list[str] = []
        sources_failed: list[str] = []

        st_result = news_result = reddit_result = None
        tasks = {
            "stocktwits": self._stocktwits.get_sentiment(ticker, company_name),
            "yfinance_news": self._news.get_sentiment(ticker, company_name),
            "reddit": self._reddit.get_sentiment(ticker, company_name),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                sources_failed.append(key)
                logger.warning("sentiment.channel.failed", channel=key, error=str(result))
            else:
                sources_used.append(key)
                if key == "stocktwits":
                    st_result = result
                elif key == "reddit":
                    reddit_result = result
                else:
                    news_result = result

        retail = self._channel_from_snapshot("retail", st_result, ["StockTwits stream"])
        social = self._channel_from_snapshot(
            "social",
            reddit_result,
            [i.text[:50] for i in (reddit_result.items[:3] if reddit_result else [])],
        )

        if news_result:
            institutional, analyst, news_ch = self._split_news_channels(news_result)
        else:
            empty = SentimentChannel(name="news", score=0, confidence=0.2, trend="stable")
            institutional = analyst = news_ch = empty

        scores = [c.score for c in [institutional, retail, social, news_ch, analyst] if c.confidence > 0.25]
        weights = [c.confidence for c in [institutional, retail, social, news_ch, analyst] if c.confidence > 0.25]
        if scores and weights:
            agg = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        else:
            agg = 0.0

        if agg >= 15:
            label = "alcista"
        elif agg <= -15:
            label = "bajista"
        else:
            label = "neutral"

        conf = sum(weights) / len(weights) if weights else 0.3

        summary = (
            f"Sentimiento de {ticker}: {label.upper()} (puntuación {agg:+.1f}). "
            f"Minorista/Social {retail.score:+.1f}, Noticias {news_ch.score:+.1f}, "
            f"Analistas {analyst.score:+.1f}, Institucional {institutional.score:+.1f}. "
            f"Fuentes: {', '.join(sources_used) or 'ninguna'}."
        )

        return SentimentEngineReport(
            ticker=ticker.upper(),
            company_name=company_name,
            aggregated_score=round(agg, 2),
            aggregated_label=label,
            confidence=round(conf, 3),
            institutional=institutional,
            retail=retail,
            social=social,
            news=news_ch,
            analyst=analyst,
            sources_used=sources_used,
            sources_failed=sources_failed,
            summary=summary,
            timestamp=datetime.now(timezone.utc),
        )
