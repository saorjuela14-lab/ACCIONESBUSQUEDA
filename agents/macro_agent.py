"""Macroeconomic analysis agent — uses verified FRED data when available."""

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
        fred_raw = snapshot.get("fred", {})

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0

        # --- FRED verified indicators ---
        self._process_fred_indicator(fred_raw, "FED_FUNDS", findings, references, risks, opportunities, score_ref := [score])
        score = score_ref[0]

        cpi_yoy = fred_raw.get("CPI_YOY") or indicators.get("CPI_YOY")
        if cpi_yoy:
            value = cpi_yoy.get("value", 0)
            ref = Reference(
                source="fred",
                data_point="CPI_YOY",
                value=value,
                url="https://fred.stlouisfed.org/series/CPIAUCSL",
            )
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Inflación CPI EE.UU. (interanual): {value:.2f}% al {cpi_yoy.get('date', 'N/D')}",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if value > 4.0:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="Inflación elevada puede retrasar recortes de tipos de la Fed",
                        confidence=0.8,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                        horizon=TimeHorizon.MONTHLY,
                    )
                )
                score -= 4
            elif value < 2.5:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Inflación acercándose al objetivo de la Fed apoya giro dovish",
                        confidence=0.7,
                        references=[ref],
                    )
                )
                score += 3

        self._process_fred_indicator(fred_raw, "UNEMPLOYMENT", findings, references, risks, opportunities, score_ref := [score])
        score = score_ref[0]

        self._process_fred_indicator(fred_raw, "GDP", findings, references, risks, opportunities, score_ref := [score])
        score = score_ref[0]

        yield_curve = fred_raw.get("YIELD_CURVE") or indicators.get("YIELD_CURVE")
        if yield_curve:
            spread = yield_curve.get("value", 0)
            ref = Reference(
                source="fred",
                data_point="T10Y2Y",
                value=spread,
                url="https://fred.stlouisfed.org/series/T10Y2Y",
            )
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Spread curva de rendimiento (10A-2A): {spread:.2f}%",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if spread < 0:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="Curva de rendimiento invertida señala riesgo de recesión",
                        confidence=0.75,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                        horizon=TimeHorizon.LONG_TERM,
                    )
                )
                score -= 6
            elif spread > 0.5:
                score += 2

        m2 = fred_raw.get("M2")
        if m2:
            ref = Reference(source="fred", data_point="M2SL", value=m2.get("value"))
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Oferta monetaria M2: ${m2['value']:,.0f}B ({m2.get('change_pct', 0) or 0:+.2f}% vs anterior)",
                    confidence=0.9,
                    references=[ref],
                )
            )

        # --- Market proxies (YFinance) ---
        vix = indicators.get("VIX", {})
        if vix and vix.get("source") != "fred":
            value = vix.get("value", 0)
            ref = Reference(source=vix.get("source", "yfinance"), data_point="VIX", value=value)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"VIX en {value:.2f} ({vix.get('change_pct', 0):+.2f}% diario)",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if value > 25:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement="VIX elevado señala miedo elevado en el mercado",
                        confidence=0.8,
                        references=[ref],
                        impact=ImpactLevel.HIGH,
                    )
                )
                score -= 5
            elif value < 15:
                score += 2
        elif fred_raw.get("VIX"):
            vix_fred = fred_raw["VIX"]
            value = vix_fred["value"]
            ref = Reference(source="fred", data_point="VIXCLS", value=value)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"VIX (FRED) en {value:.2f}",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if value > 25:
                score -= 5

        us10y = indicators.get("US10Y") or fred_raw.get("YIELD_10Y")
        if us10y:
            val = us10y.get("value", 0)
            ref = Reference(
                source=us10y.get("source", "fred"),
                data_point="US10Y",
                value=val,
            )
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"Rendimiento bonos EE.UU. 10A en {val:.2f}%",
                    confidence=0.95,
                    references=[ref],
                )
            )
            if sector in {"Real Estate", "Utilities", "Financial Services"}:
                change = us10y.get("change_pct", 0) or 0
                if change > 0:
                    risks.append(
                        Finding(
                            category=EvidenceCategory.INTERPRETATION,
                            statement=f"Subida de tipos puede presionar sector {sector}",
                            confidence=0.7,
                            references=[ref],
                            impact=ImpactLevel.MEDIUM,
                        )
                    )
                    score -= 3

        for name in ("DXY", "OIL", "GOLD", "BTC"):
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

        # --- Economic calendar (FRED releases) ---
        calendar = await self._macro.get_economic_calendar()
        for event in calendar[:5]:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT if event.get("date") else EvidenceCategory.UNCERTAINTY,
                    statement=f"Próximo: {event.get('event')} el {event.get('date', 'por definir')} (impacto: {event.get('impact')})",
                    confidence=0.85 if event.get("date") else 0.5,
                    references=[
                        Reference(
                            source="fred",
                            data_point="release",
                            value=event.get("event"),
                            url="https://fred.stlouisfed.org/releases/calendar",
                        )
                    ],
                    horizon=TimeHorizon.WEEKLY,
                )
            )

        fed_funds = fred_raw.get("FED_FUNDS")
        if fed_funds:
            rate = fed_funds["value"]
            policy_stance = "restrictiva" if rate > 3.0 else "acomodaticia" if rate < 2.0 else "neutral"
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"Postura de política monetaria: {policy_stance} (Fed Funds en {rate:.2f}%)",
                    confidence=0.8,
                    references=[Reference(source="fred", data_point="FEDFUNDS", value=rate)],
                )
            )
        else:
            findings.append(
                Finding(
                    category=EvidenceCategory.UNCERTAINTY,
                    statement="Tipo Fed Funds no disponible; configure FRED_API_KEY para datos verificados",
                    confidence=0.3,
                    references=[],
                )
            )

        has_fred = bool(fred_raw)
        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(0.75 if has_fred else 0.45),
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={"indicators": indicators, "fred": fred_raw, "sector": sector},
            summary=(
                f"Evaluación macro de {ticker} ({sector}) usando "
                f"{'FRED + datos de mercado' if has_fred else 'solo proxies de mercado'}. Puntuación: {score:.1f}."
            ),
        )

    def _process_fred_indicator(
        self,
        fred_raw: dict,
        key: str,
        findings: list[Finding],
        references: list[Reference],
        risks: list[Finding],
        opportunities: list[Finding],
        score: list[float],
    ) -> None:
        data = fred_raw.get(key)
        if not data:
            return

        value = data["value"]
        ref = Reference(
            source="fred",
            data_point=data["series_id"],
            value=value,
            url=f"https://fred.stlouisfed.org/series/{data['series_id']}",
        )
        references.append(ref)

        change_str = ""
        if data.get("change_pct") is not None:
            change_str = f" ({data['change_pct']:+.2f}% vs anterior)"

        findings.append(
            Finding(
                category=EvidenceCategory.FACT,
                statement=f"{data['label']}: {value:.2f} {data.get('unit', '')}{change_str} [al {data.get('date')}]",
                confidence=0.95,
                references=[ref],
            )
        )

        if key == "FED_FUNDS" and value > 4.0:
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement="Tipo Fed Funds restrictivo presiona activos de crecimiento",
                    confidence=0.75,
                    references=[ref],
                    impact=ImpactLevel.MEDIUM,
                )
            )
            score[0] -= 3
        elif key == "UNEMPLOYMENT" and value > 5.0:
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"Desempleo en alza ({value:.1f}%) señala enfriamiento económico",
                    confidence=0.8,
                    references=[ref],
                    impact=ImpactLevel.HIGH,
                )
            )
            score[0] -= 4
        elif key == "GDP" and data.get("change_pct") and data["change_pct"] > 0:
            score[0] += 2
