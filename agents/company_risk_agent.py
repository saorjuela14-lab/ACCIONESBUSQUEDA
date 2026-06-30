"""Company-specific risk analysis agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import NewsProvider


class CompanyRiskAgent(BaseAgent):
    name = "company_risk_agent"

    _RISK_KEYWORDS = {
        "lawsuit": "Legal proceedings detected",
        "investigation": "Regulatory investigation detected",
        "fraud": "Potential fraud reference detected",
        "recall": "Product recall risk detected",
        "layoff": "Workforce reduction signal detected",
        "ceo": "Executive leadership change signal",
        "esg": "ESG-related concern detected",
        "regulator": "Regulatory scrutiny detected",
    }

    def __init__(self, news_provider: NewsProvider) -> None:
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        company_name = kwargs.get("company_name", ticker)
        queries = [
            f"{ticker} lawsuit investigation regulator",
            f"{company_name} fraud accounting scandal",
            f"{ticker} CEO executive change ESG",
        ]

        findings: list[Finding] = []
        risks: list[Finding] = []
        references: list[Reference] = []
        score = 0.0
        detected: set[str] = set()

        for query in queries:
            items = await self._news.search_news(query, max_results=4)
            for item in items:
                text = item.title.lower()
                ref = Reference(source=item.source, url=item.url, data_point="risk_news", value=item.title)
                references.append(ref)

                matched = False
                for keyword, label in self._RISK_KEYWORDS.items():
                    if keyword in text and keyword not in detected:
                        detected.add(keyword)
                        risks.append(
                            Finding(
                                category=EvidenceCategory.RISK,
                                statement=f"{label}: {item.title}",
                                confidence=0.6,
                                references=[ref],
                                impact=ImpactLevel.HIGH,
                            )
                        )
                        score -= 10
                        matched = True
                        break

                if not matched and item.sentiment.value == "bearish":
                    risks.append(
                        Finding(
                            category=EvidenceCategory.RISK,
                            statement=item.title,
                            confidence=0.5,
                            references=[ref],
                            impact=ImpactLevel.MEDIUM,
                        )
                    )
                    score -= 4
                elif not matched:
                    findings.append(
                        Finding(
                            category=EvidenceCategory.FACT,
                            statement=item.title,
                            confidence=0.55,
                            references=[ref],
                        )
                    )

        if not risks:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement="No material company-specific risk headlines detected in recent news scan",
                    confidence=0.45,
                    references=[],
                )
            )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.45 + min(len(references), 8) * 0.05),
            findings=findings,
            risks=risks,
            opportunities=[],
            references=references,
            raw_data={"risk_keywords_detected": list(detected)},
            summary=f"Company risk scan complete. {len(risks)} risk items, {len(detected)} keyword categories.",
        )
