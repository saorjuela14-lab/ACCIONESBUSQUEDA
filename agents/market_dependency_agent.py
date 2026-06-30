"""Market dependency and correlation agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding
from providers.interfaces import MarketDataProvider
from services.correlation_service import CorrelationService


class MarketDependencyAgent(BaseAgent):
    name = "market_dependency_agent"

    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._service = CorrelationService(market_provider)

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        report = await self._service.analyze(ticker)

        findings: list[Finding] = [
            Finding(
                category=EvidenceCategory.FACT,
                statement=report.summary,
                confidence=0.75,
                references=[],
            )
        ]
        risks = []
        opportunities = []

        for macro in report.macro_sensitivities:
            if macro.sensitivity == "high":
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"{macro.factor}: {macro.scenario} — {macro.impact_if_shock}",
                        confidence=0.7,
                        references=[],
                        impact=ImpactLevel.HIGH,
                    )
                )

        for pair in report.benchmark_correlations[:5]:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Correlation with {pair.ticker}: {pair.correlation:+.2f} — {pair.interpretation}",
                    confidence=0.8,
                    references=[],
                )
            )

        for dep in report.company_dependencies[:5]:
            corr_txt = f" (corr {dep.correlation:+.2f})" if dep.correlation is not None else ""
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"{dep.ticker} [{dep.relationship}]{corr_txt}: {dep.why_it_matters}",
                    confidence=0.65,
                    references=[],
                )
            )

        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Emerging markets exposure: {report.emerging_market_exposure}",
                confidence=0.6,
                references=[],
            )
        )

        score = -report.risk_score * 0.3
        if any(p.correlation > 0.5 for p in report.benchmark_correlations if p.ticker in ("SPY", "QQQ")):
            score += 5

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=0.72,
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=[],
            raw_data=report.model_dump(mode="json"),
            summary=report.summary,
        )
