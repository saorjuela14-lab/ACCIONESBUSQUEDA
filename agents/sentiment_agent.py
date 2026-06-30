"""Sentiment analysis agent — Stocktwits, Reddit, Seeking Alpha, Yahoo."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel, NewsSentiment
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import SentimentProvider


class SentimentAgent(BaseAgent):
    name = "sentiment_agent"

    def __init__(self, sentiment_provider: SentimentProvider) -> None:
        self._sentiment = sentiment_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        snapshot = await self._sentiment.get_sentiment(ticker, company_name)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []

        if snapshot.stocktwits_bullish_pct is not None:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=(
                        f"Stocktwits labeled sentiment: {snapshot.stocktwits_bullish_pct:.0f}% bullish "
                        f"({snapshot.bullish_count} bull / {snapshot.bearish_count} bear / {snapshot.neutral_count} neutral)"
                    ),
                    confidence=0.75,
                    references=[Reference(source="stocktwits", data_point="bullish_pct", value=snapshot.stocktwits_bullish_pct)],
                )
            )

        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Collected {len(snapshot.items)} social posts from: {', '.join(snapshot.sources) or 'none'}",
                confidence=0.7 if snapshot.items else 0.2,
                references=[],
            )
        )

        for item in snapshot.items[:8]:
            ref = Reference(source=item.source, url=item.url, data_point="post", value=item.text[:120])
            references.append(ref)

            if item.sentiment == NewsSentiment.BULLISH:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=f"[{item.source}] {item.text[:200]}",
                        confidence=0.55,
                        references=[ref],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            elif item.sentiment == NewsSentiment.BEARISH:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"[{item.source}] {item.text[:200]}",
                        confidence=0.55,
                        references=[ref],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            else:
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=f"[{item.source}] {item.text[:200]}",
                        confidence=0.5,
                        references=[ref],
                    )
                )

        if not snapshot.items:
            findings.append(
                Finding(
                    category=EvidenceCategory.UNCERTAINTY,
                    statement="Limited social sentiment data retrieved",
                    confidence=0.3,
                    references=[],
                )
            )

        score = self._clamp_score(snapshot.score)
        label = "bullish" if score > 15 else "bearish" if score < -15 else "neutral"

        by_source: dict[str, int] = {}
        for item in snapshot.items:
            by_source[item.source] = by_source.get(item.source, 0) + 1

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=score,
            confidence=self._clamp_confidence(0.4 + min(len(snapshot.items), 15) * 0.035),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references[:20],
            raw_data={
                "label": label,
                "sources": snapshot.sources,
                "by_source": by_source,
                "bullish": snapshot.bullish_count,
                "bearish": snapshot.bearish_count,
                "neutral": snapshot.neutral_count,
                "stocktwits_bullish_pct": snapshot.stocktwits_bullish_pct,
            },
            summary=(
                f"Social sentiment: {label}. {len(snapshot.items)} posts from "
                f"{len(snapshot.sources)} sources. Bull {snapshot.bullish_count} / Bear {snapshot.bearish_count}."
            ),
        )
