"""Company news intelligence agent — 2-year backdrop, 3-month recent, investment impact."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, NewsSentiment, NewsTopicCategory
from domain.reports import AgentReport, Finding, NewsItem, Reference
from providers.interfaces import NewsProvider
from providers.news.intelligence import (
    build_intelligence_queries,
    dedupe_news,
    filter_relevant_news,
    group_by_category,
)
from providers.news.temporal_analysis import (
    NewsTimeline,
    build_full_summary,
    build_temporal_report,
)


class NewsAgent(BaseAgent):
    name = "news_agent"

    def __init__(self, news_provider: NewsProvider) -> None:
        self._news = news_provider

    async def _collect_timeline(
        self, ticker: str, company_name: str, sector: str | None, industry: str | None
    ) -> NewsTimeline:
        provider = self._news

        if hasattr(provider, "fetch_timeline"):
            timeline = await provider.fetch_timeline(ticker)
        else:
            timeline = NewsTimeline()
            if hasattr(provider, "get_company_news"):
                timeline.supplemental = await provider.get_company_news(ticker, max_results=20)

        queries = build_intelligence_queries(ticker, company_name, sector, industry)
        for category, query in queries[:3]:
            try:
                items = await provider.search_news(query, max_results=2, hint_category=category)
                timeline.supplemental.extend(items)
            except Exception:
                pass

        timeline.supplemental = dedupe_news(timeline.supplemental)
        return timeline

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        sector = kwargs.get("sector")
        industry = kwargs.get("industry")

        timeline = await self._collect_timeline(ticker, company_name, sector, industry)
        report = build_temporal_report(ticker, company_name, timeline)

        all_news = filter_relevant_news(timeline.all_items, ticker, company_name)
        if not all_news:
            all_news = timeline.all_items[:25]

        grouped = group_by_category(all_news)
        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []

        sections = [
            ("2Y_BACKDROP", report.two_year_summary, EvidenceCategory.FACT, 0.72),
            ("3M_RECENT", report.three_month_summary, EvidenceCategory.FACT, 0.78),
            ("ONGOING", report.ongoing_issues, EvidenceCategory.INTERPRETATION, 0.68),
            ("NARRATIVE", report.market_narrative, EvidenceCategory.INTERPRETATION, 0.7),
            ("IMPACT", report.investment_impact, EvidenceCategory.INTERPRETATION, 0.75),
        ]

        for key, statement, category, confidence in sections:
            findings.append(
                Finding(
                    category=category,
                    statement=statement,
                    confidence=confidence,
                    references=[],
                )
            )

        for item in all_news[:12]:
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
                statement=f"[{item.category.value}] {statement}",
                confidence=0.65 if item.snippet else 0.58,
                references=[ref],
                impact=item.impact,
                horizon=item.horizon,
            )

            if item.category == NewsTopicCategory.LITIGATION or item.sentiment == NewsSentiment.BEARISH:
                risks.append(finding)
            elif item.sentiment == NewsSentiment.BULLISH or item.category in {
                NewsTopicCategory.REGULATORY,
                NewsTopicCategory.PRODUCT_PIPELINE,
                NewsTopicCategory.MERGERS_ACQUISITIONS,
            }:
                opportunities.append(finding)

        full_summary = build_full_summary(report)

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(report.news_score),
            confidence=self._clamp_confidence(0.45 + min(len(all_news), 20) * 0.025),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={
                "news_count": len(all_news),
                "recent_3m_count": len(timeline.recent_3m),
                "historical_2y_count": len(timeline.historical_2y),
                "sentiment_avg_recent": report.sentiment_avg_recent,
                "sentiment_label": report.sentiment_label,
                "ongoing_themes": report.ongoing_themes,
                "two_year_summary": report.two_year_summary,
                "three_month_summary": report.three_month_summary,
                "ongoing_issues": report.ongoing_issues,
                "market_narrative": report.market_narrative,
                "investment_impact": report.investment_impact,
                "recent_by_topic": report.recent_by_topic,
                "historical_by_topic": report.historical_by_topic,
            },
            summary=full_summary,
        )
