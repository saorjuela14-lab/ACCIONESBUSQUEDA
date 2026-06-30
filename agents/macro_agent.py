"""Macroeconomic analysis agent."""

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MacroProvider, MarketDataProvider


class MacroAgent(BaseAgent):
    name = "macro_agent"

    def __init__(self, macro_provider: MacroProvider, market_provider: MarketDataProvider) -> None:
        self._macro = macro_provider
        self._market = market_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        snapshot = await self._macro.get_macro_snapshot()
        quote = await self._market.get_quote(ticker)
        sector = quote.get("sector", "Unknown")
        indicators = snapshot.get("indicators", {})

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        vix = indicators.get("VIX", {})
        if vix:
            value = vix.get("value", 0)
            ref = Reference(source="yfinance", data_point="VIX", value=value)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"VIX at {value:.2f} ({vix.get('change_pct', 0):+.2f}% daily)",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if value > 25:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="Elevated VIX signals heightened market fear",
                        confidence=0.8,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                        horizon=TimeHorizon.WEEKLY,
                    )
                )
                score -= 5
            elif value < 15:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Low VIX suggests complacent risk appetite",
                        confidence=0.65,
                        references=[ref],
                    )
                )
                score += 2

        us10y = indicators.get("US10Y", {})
        if us10y:
            ref = Reference(source="yfinance", data_point="US10Y", value=us10y.get("value"))
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"US 10Y yield at {us10y.get('value', 0):.2f}%",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if sector in {"Real Estate", "Utilities", "Financial Services"}:
                if us10y.get("change_pct", 0) > 0:
                    risks.append(
                        Finding(
                            category=EvidenceCategory.INTERPRETATION,
                            statement=f"Rising rates may pressure {sector} sector",
                            confidence=0.7,
                            references=[ref],
                            impact=ImpactLevel.MEDIUM,
                        )
                    )
                    score -= 3

        dxy = indicators.get("DXY", {})
        if dxy:
            ref = Reference(source="yfinance", data_point="DXY", value=dxy.get("value"))
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"DXY at {dxy.get('value', 0):.2f}",
                    confidence=0.9,
                    references=[ref],
                )
            )

        for name in ("OIL", "GOLD", "BTC"):
            data = indicators.get(name, {})
            if data:
                ref = Reference(source="yfinance", data_point=name, value=data.get("value"))
                references.append(ref)
                findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=f"{name}: {data.get('value', 0):.2f} ({data.get('change_pct', 0):+.2f}%)",
                        confidence=0.9,
                        references=[ref],
                    )
                )

        calendar = await self._macro.get_economic_calendar()
        for event in calendar[:3]:
            findings.append(
                Finding(
                    category=EvidenceCategory.UNCERTAINTY,
                    statement=f"Upcoming macro event: {event.get('event')} (impact: {event.get('impact')})",
                    confidence=0.5,
                    references=[Reference(source="macro_calendar", data_point="event", value=event.get("event"))],
                    horizon=TimeHorizon.WEEKLY,
                )
            )

        findings.append(
            Finding(
                category=EvidenceCategory.INTERPRETATION,
                statement="Monetary policy remains restrictive; verify latest FOMC/ECB statements",
                confidence=0.55,
                references=[Reference(source="macro_analysis", data_point="policy", value="restrictive")],
            )
        )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.6 if indicators else 0.3),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"indicators": indicators, "sector": sector},
            summary=f"Macro environment assessment for {ticker} ({sector}). Score: {score:.1f}.",
        )
