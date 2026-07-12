"""Country and sovereign risk analysis agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider, NewsProvider


class CountryRiskAgent(BaseAgent):
    name = "country_risk_agent"

    _SECTOR_COUNTRY_RISKS = {
        "Technology": ["export controls", "data sovereignty regulation"],
        "Energy": ["sanctions exposure", "OPEC policy shifts"],
        "Financial Services": ["sovereign debt stress", "rate policy divergence"],
    }

    def __init__(self, market_provider: MarketDataProvider, news_provider: NewsProvider) -> None:
        self._market = market_provider
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        quote = await self._market.get_quote(ticker)
        country = quote.get("country") or quote.get("info", {}).get("country") or "United States"
        sector = quote.get("sector", "Unknown")

        news = await self._news.search_news(f"{country} geopolitical risk sanctions 2026", max_results=5)

        findings: list[Finding] = []
        risks: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        ref = Reference(source="yfinance", data_point="country", value=country)
        references.append(ref)
        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Primary operating country: {country}",
                confidence=0.85,
                references=[ref],
            )
        )

        for risk_type in self._SECTOR_COUNTRY_RISKS.get(sector, ["general geopolitical risk"]):
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"Sector-country exposure: {risk_type}",
                    confidence=0.6,
                    references=[ref],
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.LONG_TERM,
                )
            )
            score -= 3

        for item in news:
            ref = Reference(source=item.source, url=item.url, data_point="geo_news", value=item.title)
            references.append(ref)
            if item.sentiment.value == "bearish":
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=item.title,
                        confidence=0.55,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
                score -= 5
            else:
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=item.title,
                        confidence=0.6,
                        references=[ref],
                    )
                )

        findings.append(
            Finding(
                category=EvidenceCategory.UNCERTAINTY,
                statement="Verify FX exposure, China/US/EU trade dependency with 10-K geographic revenue",
                confidence=0.45,
                references=[Reference(source="country_risk", data_point="dependency", value="unverified")],
            )
        )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.5),
            findings=findings,
            risks=risks,
            opportunities=[],
            references=references,
            raw_data={"country": country, "sector": sector},
            summary=f"Revisión de riesgo país para {country}. {len(risks)} factores de riesgo identificados.",
        )
