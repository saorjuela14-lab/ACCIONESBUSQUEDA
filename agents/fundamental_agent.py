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
            ("trailingPE", "Ratio P/E", lambda v: -5 if v > 35 else (5 if v < 15 else 0), 15, 35),
            ("forwardPE", "P/E forward", lambda v: -3 if v > 30 else (3 if v < 12 else 0), 12, 30),
            ("priceToBook", "P/B", lambda v: -2 if v > 5 else (2 if v < 1.5 else 0), 1.5, 5),
            ("returnOnEquity", "ROE", lambda v: 8 if v > 0.15 else (-5 if v < 0.05 else 0), 0.05, 0.15),
            ("returnOnAssets", "ROA", lambda v: 4 if v > 0.08 else (-3 if v < 0.02 else 0), 0.02, 0.08),
            ("debtToEquity", "Deuda/Patrimonio", lambda v: -8 if v > 2 else (4 if v < 0.5 else 0), 0.5, 2),
            ("currentRatio", "Ratio corriente", lambda v: 4 if v > 1.5 else (-6 if v < 1 else 0), 1, 1.5),
            ("quickRatio", "Ratio rápido", lambda v: 3 if v > 1 else (-4 if v < 0.8 else 0), 0.8, 1),
            ("profitMargins", "Margen neto", lambda v: 5 if v > 0.15 else (-3 if v < 0.05 else 0), 0.05, 0.15),
            ("operatingMargins", "Margen operativo", lambda v: 4 if v > 0.2 else (-3 if v < 0.08 else 0), 0.08, 0.2),
            ("revenueGrowth", "Crecimiento ingresos", lambda v: 8 if v > 0.1 else (-5 if v < 0 else 0), 0, 0.1),
            ("earningsGrowth", "Crecimiento BPA", lambda v: 8 if v > 0.1 else (-5 if v < 0 else 0), 0, 0.1),
            ("freeCashflow", "Flujo de caja libre", lambda v: 6 if v > 0 else -8, 0, 1),
            ("dividendYield", "Rendimiento dividendo", lambda v: 3 if 0.02 < v < 0.06 else 0, 0.02, 0.06),
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
                        statement=f"{label} en {value} indica estrés en el balance",
                        confidence=0.75,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
            elif key in {"returnOnEquity", "revenueGrowth", "earningsGrowth"} and isinstance(value, (int, float)) and value > high:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=f"{label} sólido respalda perfil de crecimiento de calidad",
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
                    statement=f"Consenso analistas: {recommendation_key.upper()}",
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
                    statement=f"Precio objetivo medio implica {upside:+.1f}% de upside",
                    confidence=0.6,
                    references=[Reference(source="yfinance", data_point="targetMeanPrice", value=target)],
                )
            )
            if upside > 15:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="El objetivo consensuado sugiere upside material vs precio actual",
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
                f"Revisión fundamental de {quote.get('company_name', ticker)} "
                f"basada en {metrics_used} métricas verificadas. Puntuación: {normalized_score:.1f}."
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
