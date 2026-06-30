"""Fundamental analysis agent."""

from typing import Any

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider


class FundamentalAgent(BaseAgent):
    name = "fundamental_agent"

    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        financials = await self._market.get_financials(ticker)
        quote = await self._market.get_quote(ticker)
        info = financials.get("info", {})
        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0
        metrics_used = 0

        metric_defs = [
            ("trailingPE", "P/E Ratio", lambda v: -5 if v > 35 else (5 if v < 15 else 0), 15, 35),
            ("forwardPE", "Forward P/E", lambda v: -3 if v > 30 else (3 if v < 12 else 0), 12, 30),
            ("priceToBook", "P/B", lambda v: -2 if v > 5 else (2 if v < 1.5 else 0), 1.5, 5),
            ("returnOnEquity", "ROE", lambda v: 8 if v > 0.15 else (-5 if v < 0.05 else 0), 0.05, 0.15),
            ("returnOnAssets", "ROA", lambda v: 4 if v > 0.08 else (-3 if v < 0.02 else 0), 0.02, 0.08),
            ("debtToEquity", "Debt/Equity", lambda v: -8 if v > 2 else (4 if v < 0.5 else 0), 0.5, 2),
            ("currentRatio", "Current Ratio", lambda v: 4 if v > 1.5 else (-6 if v < 1 else 0), 1, 1.5),
            ("quickRatio", "Quick Ratio", lambda v: 3 if v > 1 else (-4 if v < 0.8 else 0), 0.8, 1),
            ("profitMargins", "Net Margin", lambda v: 5 if v > 0.15 else (-3 if v < 0.05 else 0), 0.05, 0.15),
            ("operatingMargins", "Operating Margin", lambda v: 4 if v > 0.2 else (-3 if v < 0.08 else 0), 0.08, 0.2),
            ("revenueGrowth", "Revenue Growth", lambda v: 8 if v > 0.1 else (-5 if v < 0 else 0), 0, 0.1),
            ("earningsGrowth", "EPS Growth", lambda v: 8 if v > 0.1 else (-5 if v < 0 else 0), 0, 0.1),
            ("freeCashflow", "Free Cash Flow", lambda v: 6 if v > 0 else -8, 0, 1),
            ("dividendYield", "Dividend Yield", lambda v: 3 if 0.02 < v < 0.06 else 0, 0.02, 0.06),
            ("pegRatio", "PEG", lambda v: 6 if 0 < v < 1 else (-4 if v > 2 else 0), 0, 2),
        ]

        for key, label, scorer, low, high in metric_defs:
            value = info.get(key)
            if value is None:
                continue
            metrics_used += 1
            score += scorer(value)
            ref = Reference(source="yfinance", data_point=key, value=value)
            references.append(ref)

            category = EvidenceCategory.FACT
            statement = f"{label}: {value}"
            if key in {"debtToEquity", "currentRatio", "quickRatio"} and (
                (isinstance(value, (int, float)) and value > high)
                or (isinstance(value, (int, float)) and value < low)
            ):
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"{label} at {value} indicates balance sheet stress",
                        confidence=0.75,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
            elif key in {"returnOnEquity", "revenueGrowth", "earningsGrowth"} and isinstance(value, (int, float)) and value > high:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=f"Strong {label.lower()} supports quality growth profile",
                        confidence=0.7,
                        references=[ref],
                        impact=ImpactLevel.MEDIUM,
                    )
                )

            findings.append(
                Finding(
                    category=category,
                    statement=statement,
                    confidence=0.9,
                    references=[ref],
                )
            )

        recommendation_key = info.get("recommendationKey")
        if recommendation_key:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Analyst consensus: {recommendation_key.upper()}",
                    confidence=0.85,
                    references=[Reference(source="yfinance", data_point="recommendationKey", value=recommendation_key)],
                )
            )

        target = info.get("targetMeanPrice")
        current = quote.get("current_price")
        if target and current:
            upside = ((target - current) / current) * 100
            findings.append(
                Finding(
                    category=EvidenceCategory.PROBABILITY,
                    statement=f"Analyst mean target implies {upside:+.1f}% upside",
                    confidence=0.6,
                    references=[Reference(source="yfinance", data_point="targetMeanPrice", value=target)],
                )
            )
            if upside > 15:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Consensus target suggests material upside vs current price",
                        confidence=0.55,
                        references=[Reference(source="yfinance", data_point="targetMeanPrice", value=target)],
                    )
                )

        confidence = self._clamp_confidence(0.4 + min(metrics_used, 12) * 0.04)
        normalized_score = self._clamp_score(score)

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=normalized_score,
            confidence=confidence,
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"info": self._sanitize(info), "company": quote.get("company_name")},
            summary=(
                f"Fundamental review for {quote.get('company_name', ticker)} "
                f"based on {metrics_used} verified metrics. Score: {normalized_score:.1f}."
            ),
        )

    def _sanitize(self, info: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "trailingPE", "forwardPE", "priceToBook", "returnOnEquity", "returnOnAssets",
            "debtToEquity", "currentRatio", "quickRatio", "profitMargins", "operatingMargins",
            "revenueGrowth", "earningsGrowth", "freeCashflow", "dividendYield", "pegRatio",
            "targetMeanPrice", "recommendationKey", "sector", "industry",
        }
        return {k: info.get(k) for k in allowed if k in info}
