"""Sentiment analysis agent — multi-channel sentiment engine."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from services.sentiment_engine_service import SentimentEngineService


class SentimentAgent(BaseAgent):
    name = "sentiment_agent"

    def __init__(self, engine: SentimentEngineService | None = None) -> None:
        self._engine = engine or SentimentEngineService()

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        report = await self._engine.analyze(ticker, company_name)

        findings: list[Finding] = [
            Finding(
                category=EvidenceCategory.INTERPRETATION,
                statement=report.summary,
                confidence=report.confidence,
                references=[],
            ),
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Institutional {report.institutional.score:+.1f} | Retail {report.retail.score:+.1f} | Social {report.social.score:+.1f} | News {report.news.score:+.1f} | Analyst {report.analyst.score:+.1f}",
                confidence=report.confidence,
                references=[],
            ),
        ]

        for ch in [report.institutional, report.retail, report.news, report.analyst]:
            for factor in ch.top_factors[:2]:
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=f"[{ch.name}] {factor}",
                        confidence=ch.confidence,
                        references=[Reference(source=ch.name, data_point="factor", value=factor)],
                    )
                )

        risks, opportunities = [], []
        if report.aggregated_score < -10:
            risks.append(Finding(category=EvidenceCategory.RISK, statement="Aggregate sentiment bearish", confidence=0.6, references=[]))
        elif report.aggregated_score > 10:
            opportunities.append(Finding(category=EvidenceCategory.INTERPRETATION, statement="Aggregate sentiment bullish", confidence=0.6, references=[]))

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(report.aggregated_score),
            confidence=self._clamp_confidence(report.confidence),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=[],
            raw_data=report.model_dump(mode="json"),
            summary=report.summary,
        )
