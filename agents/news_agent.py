"""Company news intelligence agent — actualidad, M&A, litigation, regulatory, pipeline."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, NewsSentiment, NewsTopicCategory
from domain.reports import AgentReport, Finding, NewsItem, Reference
from providers.interfaces import NewsProvider
from providers.news.intelligence import (
    build_actualidad_summary,
    build_intelligence_queries,
    dedupe_news,
    group_by_category,
    score_from_developments,
)


class NewsAgent(BaseAgent):
    name = "news_agent"

    def __init__(self, news_provider: NewsProvider) -> None:
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        sector = kwargs.get("sector")
        industry = kwargs.get("industry")

        queries = build_intelligence_queries(ticker, company_name, sector, industry)
        all_news: list[NewsItem] = []

        for category, query in queries:
            items = await self._news.search_news(query, max_results=4, hint_category=category)
            all_news.extend(items)

        unique_news = dedupe_news(all_news)
        grouped = group_by_category(unique_news)
        actualidad = build_actualidad_summary(ticker, company_name, grouped)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []

        for category, items in grouped.items():
            if not items:
                continue
            for item in items[:3]:
                ref = Reference(
                    source=item.source,
                    url=item.url,
                    data_point=item.category.value,
                    value=item.title,
                )
                references.append(ref)
                statement = item.title
                if item.snippet:
                    statement = f"{item.title} — {item.snippet[:180]}"

                finding = Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"[{category.value}] {statement}",
                    confidence=0.65 if item.snippet else 0.58,
                    references=[ref],
                    impact=item.impact,
                    horizon=item.horizon,
                )

                if category == NewsTopicCategory.LITIGATION or item.sentiment == NewsSentiment.BEARISH:
                    risks.append(finding)
                elif item.sentiment == NewsSentiment.BULLISH or category in {
                    NewsTopicCategory.REGULATORY,
                    NewsTopicCategory.PRODUCT_PIPELINE,
                    NewsTopicCategory.MERGERS_ACQUISITIONS,
                }:
                    opportunities.append(finding)
                else:
                    findings.append(finding)

        if actualidad:
            findings.insert(
                0,
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=actualidad,
                    confidence=0.7 if unique_news else 0.35,
                    references=references[:5],
                ),
            )

        score = score_from_developments(grouped)
        topic_counts = {cat.value: len(items) for cat, items in grouped.items() if items}

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.35 + min(len(unique_news), 12) * 0.05),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={
                "news_count": len(unique_news),
                "topic_counts": topic_counts,
                "actualidad_summary": actualidad,
                "grouped_headlines": {
                    cat.value: [item.title for item in items[:5]]
                    for cat, items in grouped.items()
                    if items
                },
            },
            summary=actualidad if unique_news else f"No recent company developments found for {company_name} ({ticker}).",
        )
