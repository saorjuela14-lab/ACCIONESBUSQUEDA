"""Corporate actions detection agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider, NewsProvider


class CorporateActionsAgent(BaseAgent):
    name = "corporate_actions_agent"

    def __init__(self, market_provider: MarketDataProvider, news_provider: NewsProvider) -> None:
        self._market = market_provider
        self._news = news_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        financials = await self._market.get_financials(ticker)
        info = financials.get("info", {})
        news = await self._news.search_news(f"{ticker} dividend split buyback acquisition insider", max_results=8)

        findings: list[Finding] = []
        opportunities: list[Finding] = []
        risks: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        div_yield = info.get("dividendYield")
        if div_yield:
            ref = Reference(source="yfinance", data_point="dividendYield", value=div_yield)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Dividend yield: {div_yield * 100:.2f}%",
                    confidence=0.9,
                    references=[ref],
                )
            )
            if div_yield > 0.02:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Active dividend policy supports income investors",
                        confidence=0.7,
                        references=[ref],
                    )
                )
                score += 5

        payout = info.get("payoutRatio")
        if payout and payout > 0.8:
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"High payout ratio ({payout * 100:.0f}%) may limit dividend sustainability",
                    confidence=0.65,
                    references=[Reference(source="yfinance", data_point="payoutRatio", value=payout)],
                    impact=ImpactLevel.MEDIUM,
                )
            )
            score -= 5

        for item in news:
            title = item.title.lower()
            ref = Reference(source=item.source, url=item.url, data_point="corporate_action", value=item.title)
            references.append(ref)
            if any(k in title for k in ("buyback", "repurchase")):
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=item.title,
                        confidence=0.7,
                        references=[ref],
                    )
                )
                score += 6
            elif any(k in title for k in ("split", "secondary offering", "ipo")):
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=item.title,
                        confidence=0.75,
                        references=[ref],
                    )
                )
            elif any(k in title for k in ("insider buying", "insider purchase")):
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=item.title,
                        confidence=0.65,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
                score += 8
            elif any(k in title for k in ("insider selling", "insider sale")):
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=item.title,
                        confidence=0.65,
                        references=[ref],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
                score -= 6
            elif any(k in title for k in ("merger", "acquisition", "m&a")):
                findings.append(
                    Finding(
                        category=EvidenceCategory.UNCERTAINTY,
                        statement=item.title,
                        confidence=0.6,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.5 + min(len(references), 6) * 0.06),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"dividend_yield": div_yield, "payout_ratio": payout},
            summary=f"Corporate actions scan. Score {score:.1f}.",
        )
