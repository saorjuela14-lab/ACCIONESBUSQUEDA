"""Sentiment analysis agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import NewsProvider


class SentimentAgent(BaseAgent):
    name = "sentiment_agent"

    _POSITIVE = {
        "buy", "bullish", "outperform", "upgrade", "strong buy", "accumulate",
        "beat", "surge", "rally", "optimistic", "growth",
    }
    _NEGATIVE = {
        "sell", "bearish", "underperform", "downgrade", "strong sell",
        "miss", "decline", "concern", "risk", "lawsuit", "investigation",
    }

    def __init__(self, news_provider: NewsProvider) -> None:
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        queries = [
            f"{ticker} reddit stocktwits sentiment",
            f"{ticker} investor sentiment forecast",
            f"{company_name} seeking alpha opinion",
        ]

        texts: list[str] = []
        references: list[Reference] = []
        for query in queries:
            items = await self._news.search_news(query, max_results=4)
            for item in items:
                texts.append(item.title)
                references.append(Reference(source=item.source, url=item.url, data_point="sentiment_text", value=item.title))

        combined = " ".join(texts).lower()
        pos_hits = [w for w in self._POSITIVE if w in combined]
        neg_hits = [w for w in self._NEGATIVE if w in combined]
        score_raw = len(pos_hits) - len(neg_hits)
        score = self._clamp_score(score_raw * 8)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []

        if pos_hits:
            opportunities.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"Positive sentiment signals detected: {', '.join(pos_hits[:5])}",
                    confidence=0.55,
                    references=references[:3],
                    impact=ImpactLevel.MEDIUM,
                )
            )
        if neg_hits:
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"Negative sentiment signals detected: {', '.join(neg_hits[:5])}",
                    confidence=0.55,
                    references=references[:3],
                    impact=ImpactLevel.MEDIUM,
                )
            )

        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Sentiment score derived from {len(texts)} public sources",
                confidence=0.5 if texts else 0.2,
                references=references[:1] if references else [],
            )
        )

        if not texts:
            findings.append(
                Finding(
                    category=EvidenceCategory.UNCERTAINTY,
                    statement="Insufficient public sentiment data; social APIs recommended for production",
                    confidence=0.3,
                    references=[],
                )
            )

        label = "bullish" if score > 15 else "bearish" if score < -15 else "neutral"

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=score,
            confidence=self._clamp_confidence(0.35 + min(len(texts), 8) * 0.06),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"positive_hits": pos_hits, "negative_hits": neg_hits, "label": label},
            summary=f"Aggregate sentiment: {label}. Score {score:.1f} from {len(texts)} sources.",
        )
