"""News collection and classification agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, NewsItem, Reference
from providers.interfaces import NewsProvider


class NewsAgent(BaseAgent):
    name = "news_agent"

    def __init__(self, news_provider: NewsProvider) -> None:
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        queries = [
            f"{ticker} stock news",
            f"{company_name} earnings regulatory",
            f"{ticker} analyst upgrade downgrade",
        ]

        all_news: list[NewsItem] = []
        for query in queries:
            items = await self._news.search_news(query, max_results=5)
            all_news.extend(items)

        seen: set[str] = set()
        unique_news: list[NewsItem] = []
        for item in all_news:
            key = item.title.lower().strip()
            if key not in seen:
                seen.add(key)
                unique_news.append(item)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        for item in unique_news[:12]:
            ref = Reference(source=item.source, url=item.url, data_point="headline", value=item.title)
            references.append(ref)

            if item.sentiment.value == "bullish":
                score += 3
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=item.title,
                        confidence=0.55,
                        references=[ref],
                        impact=item.impact,
                        horizon=item.horizon,
                    )
                )
            elif item.sentiment.value == "bearish":
                score -= 3
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=item.title,
                        confidence=0.55,
                        references=[ref],
                        impact=item.impact,
                        horizon=item.horizon,
                    )
                )
            else:
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=item.title,
                        confidence=0.6,
                        references=[ref],
                        impact=ImpactLevel.LOW,
                        horizon=item.horizon,
                    )
                )

        bullish = sum(1 for n in unique_news if n.sentiment.value == "bullish")
        bearish = sum(1 for n in unique_news if n.sentiment.value == "bearish")

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.4 + min(len(unique_news), 10) * 0.05),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"news_count": len(unique_news), "bullish": bullish, "bearish": bearish},
            summary=f"Collected {len(unique_news)} news items. Bullish: {bullish}, Bearish: {bearish}.",
        )
