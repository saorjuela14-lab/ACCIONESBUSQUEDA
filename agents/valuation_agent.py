"""Valuation analysis agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider


class ValuationAgent(BaseAgent):
    name = "valuation_agent"

    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        financials = await self._market.get_financials(ticker)
        quote = await self._market.get_quote(ticker)
        info = financials.get("info", {})
        price = float(quote.get("current_price") or info.get("currentPrice") or 0)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        pe = info.get("trailingPE")
        fpe = info.get("forwardPE")
        pb = info.get("priceToBook")
        target = info.get("targetMeanPrice")
        fcf = info.get("freeCashflow")
        shares = info.get("sharesOutstanding")

        intrinsic_value = None
        if fcf and shares and shares > 0:
            fcf_per_share = fcf / shares
            intrinsic_value = fcf_per_share * 15
            ref = Reference(source="valuation", data_point="intrinsic_value_dcf_proxy", value=round(intrinsic_value, 2))
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"DCF proxy intrinsic value: ${intrinsic_value:.2f} (15x FCF/share)",
                    confidence=0.5,
                    references=[ref],
                )
            )

        fair_value = target or intrinsic_value
        margin_of_safety = None
        if fair_value and price:
            margin_of_safety = ((fair_value - price) / fair_value) * 100
            ref = Reference(source="valuation", data_point="margin_of_safety", value=round(margin_of_safety, 2))
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.PROBABILITY,
                    statement=f"Margin of safety: {margin_of_safety:+.1f}% vs fair value ${fair_value:.2f}",
                    confidence=0.55,
                    references=[ref],
                )
            )
            if margin_of_safety > 20:
                score += 20
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Significant undervaluation vs fair value estimate",
                        confidence=0.6,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
            elif margin_of_safety < -20:
                score -= 20
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="Potential overvaluation vs fair value estimate",
                        confidence=0.6,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )

        for metric, value, cheap, expensive in [
            ("P/E", pe, 12, 30),
            ("Forward P/E", fpe, 10, 25),
            ("P/B", pb, 1.5, 4),
        ]:
            if value is None:
                continue
            ref = Reference(source="yfinance", data_point=metric, value=value)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"{metric}: {value:.2f}",
                    confidence=0.9,
                    references=[ref],
                )
            )
            if value < cheap:
                score += 8
            elif value > expensive:
                score -= 8

        peers = await self._market.get_peers(ticker)
        if peers:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Peer set for comparison: {', '.join(peers[:5])}",
                    confidence=0.7,
                    references=[Reference(source="yfinance", data_point="peers", value=",".join(peers[:5]))],
                )
            )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.55 if fair_value else 0.35),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={
                "price": price,
                "intrinsic_value": intrinsic_value,
                "fair_value": fair_value,
                "margin_of_safety_pct": margin_of_safety,
            },
            summary=f"Valuation assessment. MOS: {margin_of_safety:.1f}%." if margin_of_safety is not None else "Valuation assessment with limited fair value data.",
        )
