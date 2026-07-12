"""Portfolio analysis and risk management agent."""

from collections import defaultdict

from agents.base import BaseAgent
from domain.entities import Portfolio
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider


class PortfolioAgent(BaseAgent):
    name = "portfolio_agent"

    def __init__(self, market_provider: MarketDataProvider, max_concentration_pct: float = 25.0) -> None:
        self._market = market_provider
        self._max_concentration = max_concentration_pct

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        portfolio: Portfolio | None = kwargs.get("portfolio")
        if portfolio is None:
            return AgentReport(
                agent_name=self.name,
                ticker=ticker.upper(),
                score=0.0,
                confidence=0.2,
                findings=[
                    Finding(
                        category=EvidenceCategory.UNCERTAINTY,
                        statement="No portfolio context provided for concentration analysis",
                        confidence=0.2,
                        references=[],
                    )
                ],
                summary="Contexto de portafolio no disponible.",
            )

        total_value = portfolio.total_value or portfolio.initial_capital
        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        sector_exposure: dict[str, float] = defaultdict(float)
        for position in portfolio.positions:
            quote = await self._market.get_quote(position.ticker)
            price = quote.get("current_price") or position.average_cost
            position.current_price = float(price)
            value = position.shares * float(price)
            weight = (value / total_value) * 100 if total_value else 0
            sector = quote.get("sector") or "Unknown"
            sector_exposure[sector] += weight

            ref = Reference(source="portfolio", data_point="position_weight", value=round(weight, 2))
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"{position.ticker}: {weight:.1f}% of portfolio",
                    confidence=0.9,
                    references=[ref],
                )
            )

        proposed_quote = await self._market.get_quote(ticker)
        proposed_sector = proposed_quote.get("sector") or "Unknown"
        current_sector_weight = sector_exposure.get(proposed_sector, 0.0)
        projected = current_sector_weight + 5.0

        if projected > self._max_concentration:
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=(
                        f"Adding {ticker} may increase {proposed_sector} exposure to ~{projected:.1f}% "
                        f"(limit {self._max_concentration:.0f}%)"
                    ),
                    confidence=0.75,
                    references=[Reference(source="portfolio", data_point="sector_exposure", value=projected)],
                    impact=ImpactLevel.HIGH,
                )
            )
            score -= 15
            opportunities.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement="Consider diversifying into uncorrelated sectors or ETFs",
                    confidence=0.6,
                    references=[],
                )
            )
        else:
            score += 5
            opportunities.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"Proposed allocation within diversification limits for {proposed_sector}",
                    confidence=0.65,
                    references=[],
                )
            )

        return_pct = portfolio.return_pct
        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"Portfolio '{portfolio.name}' return: {return_pct:+.2f}%",
                confidence=0.95,
                references=[Reference(source="portfolio", data_point="return_pct", value=return_pct)],
            )
        )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=0.7,
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={
                "sector_exposure": dict(sector_exposure),
                "total_value": total_value,
                "cash_pct": (portfolio.cash / total_value * 100) if total_value else 0,
            },
            summary=f"Análisis de encaje en portafolio para {ticker} en '{portfolio.name}'.",
        )
