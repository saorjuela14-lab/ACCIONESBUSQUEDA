"""Orchestrates social + news discovery and optional committee analysis."""

import asyncio
from collections import defaultdict

from domain.discovery import (
    DiscoveryAnalyzeResult,
    DiscoveryCandidate,
    DiscoveryMention,
    DiscoveryReport,
)
from domain.reports import InvestmentThesis
from providers.discovery.news_scanner import NewsDiscoveryScanner
from providers.discovery.reddit_discovery import RedditDiscoveryScanner
from providers.discovery.stocktwits_trending import StockTwitsTrendingScanner
from providers.discovery.x_search_provider import XSearchScanner
from providers.interfaces import MarketDataProvider
from services.analysis_service import AnalysisService
from utils.logging import get_logger

logger = get_logger(__name__)

_SOURCE_WEIGHTS = {
    "stocktwits": 1.2,
    "x": 1.0,
    "reddit": 0.9,
    "news": 1.1,
}

_SENTIMENT_SCORE = {
    "bullish": 1.0,
    "bearish": -0.5,
    "neutral": 0.0,
}


class CompanyDiscoveryService:
    """Scans X, Reddit, StockTwits and news to surface new tickers."""

    def __init__(
        self,
        market_provider: MarketDataProvider,
        analysis_service: AnalysisService | None = None,
    ) -> None:
        self._market = market_provider
        self._analysis = analysis_service
        self._stocktwits = StockTwitsTrendingScanner()
        self._x = XSearchScanner()
        self._reddit = RedditDiscoveryScanner()
        self._news = NewsDiscoveryScanner()

    async def research(
        self,
        themes: list[str] | None = None,
        max_candidates: int = 15,
        exclude_tickers: list[str] | None = None,
        max_price: float | None = None,
    ) -> DiscoveryReport:
        themes = themes or [
            "growth stocks",
            "biotech emergente",
            "semiconductores IA",
        ]
        exclude = {t.upper() for t in (exclude_tickers or [])}

        logger.info("discovery.research.start", themes=themes, max_price=max_price)

        st, x_hits, reddit_hits, news_hits = await asyncio.gather(
            self._stocktwits.scan(),
            self._x.scan(extra_queries=themes),
            self._reddit.scan(extra_queries=themes),
            self._news.scan(themes=themes),
            return_exceptions=True,
        )

        raw_pairs: list[tuple[str, DiscoveryMention]] = []
        sources_scanned: list[str] = []

        for label, batch in (
            ("stocktwits", st),
            ("x", x_hits),
            ("reddit", reddit_hits),
            ("news", news_hits),
        ):
            if isinstance(batch, Exception):
                logger.warning("discovery.source_failed", source=label, error=str(batch))
                continue
            sources_scanned.append(label)
            raw_pairs.extend(batch)

        aggregated: dict[str, list[DiscoveryMention]] = defaultdict(list)
        for ticker, mention in raw_pairs:
            if not ticker:
                continue
            ticker = ticker.upper()
            if ticker in exclude:
                continue
            aggregated[ticker].append(mention)

        validated = await self._validate_tickers(list(aggregated.keys()))
        if max_price is not None:
            validated = {
                t: q for t, q in validated.items()
                if float(q.get("current_price") or 0) <= max_price
                and float(q.get("current_price") or 0) > 0
            }
        candidates = self._rank_candidates(aggregated, validated)
        candidates = candidates[:max_candidates]

        total_mentions = sum(c.mention_count for c in candidates)
        summary = self._build_summary(candidates, sources_scanned, themes)
        if max_price is not None:
            summary += f" Filtro de precio: ≤ ${max_price:.2f}."

        report = DiscoveryReport(
            query_themes=themes,
            candidates=candidates,
            summary=summary,
            sources_scanned=sources_scanned,
            total_mentions_found=total_mentions,
        )
        logger.info("discovery.research.done", candidates=len(candidates))
        return report

    async def research_and_analyze(
        self,
        themes: list[str] | None = None,
        max_candidates: int = 15,
        analyze_top: int = 3,
        exclude_tickers: list[str] | None = None,
        portfolio=None,
        watchlist=None,
    ) -> DiscoveryAnalyzeResult:
        report = await self.research(
            themes=themes,
            max_candidates=max_candidates,
            exclude_tickers=exclude_tickers,
        )

        analyses: list[InvestmentThesis] = []
        if self._analysis and report.candidates:
            top = report.candidates[:analyze_top]
            tasks = [
                self._analysis.analyze_ticker(
                    c.ticker,
                    portfolio=portfolio,
                    watchlist=watchlist,
                )
                for c in top
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for candidate, result in zip(top, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "discovery.analyze_failed",
                        ticker=candidate.ticker,
                        error=str(result),
                    )
                    continue
                analyses.append(result)

        recommendation = self._build_recommendation(report, analyses)
        return DiscoveryAnalyzeResult(
            discovery=report,
            analyses=analyses,
            recommendation_summary=recommendation,
        )

    async def _validate_tickers(self, tickers: list[str]) -> dict[str, dict]:
        """Keep tickers that resolve to a real quote with a price or name."""
        validated: dict[str, dict] = {}

        async def _check(ticker: str) -> None:
            try:
                quote = await self._market.get_quote(ticker)
                price = quote.get("current_price")
                name = quote.get("company_name", ticker)
                if price or (name and name.upper() != ticker):
                    validated[ticker] = quote
            except Exception:
                pass

        await asyncio.gather(*[_check(t) for t in tickers[:40]])
        return validated

    def _rank_candidates(
        self,
        aggregated: dict[str, list[DiscoveryMention]],
        validated: dict[str, dict],
    ) -> list[DiscoveryCandidate]:
        candidates: list[DiscoveryCandidate] = []

        for ticker, mentions in aggregated.items():
            if ticker not in validated:
                continue

            quote = validated[ticker]
            sources = sorted({m.source for m in mentions})
            source_score = sum(_SOURCE_WEIGHTS.get(s, 0.5) for s in sources)
            mention_score = min(len(mentions) * 0.8, 8.0)

            sentiment_vals = [
                _SENTIMENT_SCORE.get((m.sentiment or "").lower(), 0.0)
                for m in mentions
                if m.sentiment
            ]
            sentiment_score = (
                sum(sentiment_vals) / len(sentiment_vals) if sentiment_vals else None
            )
            sent_bonus = (sentiment_score or 0) * 2

            news_headlines = [
                m.text[:120]
                for m in mentions
                if m.source == "news"
            ][:5]

            score = round(source_score + mention_score + sent_bonus, 2)
            rationale = self._candidate_rationale(ticker, mentions, sources, quote)

            candidates.append(
                DiscoveryCandidate(
                    ticker=ticker,
                    company_name=quote.get("company_name"),
                    score=score,
                    mention_count=len(mentions),
                    sources=sources,
                    sentiment_score=round(sentiment_score, 2) if sentiment_score is not None else None,
                    news_headlines=news_headlines,
                    mentions=mentions[:8],
                    rationale=rationale,
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _candidate_rationale(self, ticker: str, mentions: list[DiscoveryMention], sources: list[str], quote: dict) -> str:
        name = quote.get("company_name", ticker)
        src_es = {"stocktwits": "StockTwits", "x": "X/Twitter", "reddit": "Reddit", "news": "noticias"}
        src_labels = ", ".join(src_es.get(s, s) for s in sources)
        return (
            f"{name} ({ticker}) aparece en {len(mentions)} menciones "
            f"en {src_labels}. Sector: {quote.get('sector') or 'N/D'}."
        )

    def _build_summary(
        self,
        candidates: list[DiscoveryCandidate],
        sources: list[str],
        themes: list[str],
    ) -> str:
        if not candidates:
            return (
                "No se encontraron candidatos válidos en esta búsqueda. "
                "Prueba otros temas o repite más tarde."
            )

        top = candidates[:5]
        lines = [
            f"Investigación en {', '.join(sources)} sobre: {', '.join(themes)}.",
            f"Se identificaron {len(candidates)} empresas con tickers válidos.",
            "Destacados: "
            + "; ".join(f"{c.ticker} ({c.mention_count} menciones, score {c.score})" for c in top)
            + ".",
        ]
        return " ".join(lines)

    def _build_recommendation(
        self,
        report: DiscoveryReport,
        analyses: list[InvestmentThesis],
    ) -> str:
        if not analyses:
            if not report.candidates:
                return report.summary
            top = report.candidates[0]
            return (
                f"Sin análisis profundo disponible. Candidato principal por buzz social: "
                f"{top.ticker} ({top.company_name or '—'}) — {top.rationale}"
            )

        rec_map = {
            "strong_buy": "Compra fuerte",
            "buy": "Compra",
            "hold": "Mantener",
            "sell": "Venta",
            "strong_sell": "Venta fuerte",
        }

        parts = [report.summary, "", "Análisis del comité sobre descubiertos:"]
        for thesis in analyses:
            rec = rec_map.get(thesis.recommendation.value, thesis.recommendation.value)
            conf = f"{thesis.confidence * 100:.0f}%"
            parts.append(
                f"• {thesis.ticker}: {rec} (confianza {conf}) — "
                f"{thesis.executive_summary[:200]}…"
            )

        best = max(analyses, key=lambda t: t.confidence)
        best_rec = rec_map.get(best.recommendation.value, best.recommendation.value)
        parts.append(
            f"\nRecomendación principal: {best.ticker} ({best_rec}, confianza {best.confidence * 100:.0f}%)."
        )
        return "\n".join(parts)
