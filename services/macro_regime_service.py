"""Macro regime desk — FRED + market proxies → risk mode and sizing."""

from __future__ import annotations

from typing import Any

from domain.risk import MacroIndicatorSnapshot, MacroRegimeAssessment, RiskMode
from providers.interfaces import MacroProvider
from providers.macro.factory import get_macro_provider
from utils.logging import get_logger

logger = get_logger(__name__)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_of(value: Any) -> str | None:
    if isinstance(value, dict):
        d = value.get("date")
        return str(d) if d else None
    return None


class MacroRegimeService:
    """Builds a single regime assessment for recommendations and risk gates."""

    def __init__(self, macro_provider: MacroProvider | None = None) -> None:
        self._macro = macro_provider or get_macro_provider()

    async def assess(self, market_regime: str | None = None) -> MacroRegimeAssessment:
        indicators: list[MacroIndicatorSnapshot] = []
        risks: list[str] = []
        opportunities: list[str] = []
        score = 0.0

        snapshot: dict[str, Any] = {}
        try:
            snapshot = await self._macro.get_macro_snapshot()
        except Exception as exc:
            logger.warning("macro_regime.snapshot_failed", error=str(exc))

        fred = snapshot.get("fred") or {}
        yf_ind = snapshot.get("indicators") or {}

        fed = _num(fred.get("FED_FUNDS") or yf_ind.get("FED_FUNDS"))
        cpi = _num(fred.get("CPI_YOY") or yf_ind.get("CPI_YOY"))
        unemp = _num(fred.get("UNEMPLOYMENT") or yf_ind.get("UNEMPLOYMENT"))
        curve = _num(fred.get("YIELD_CURVE") or yf_ind.get("YIELD_CURVE") or yf_ind.get("T10Y2Y"))
        vix = _num(yf_ind.get("VIX") or fred.get("VIXCLS") or yf_ind.get("^VIX"))

        # --- Fed funds / policy stance ---
        if fed is not None:
            if fed >= 5.0:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="FED_FUNDS",
                        value=fed,
                        date=_date_of(fred.get("FED_FUNDS")),
                        signal="bearish",
                        note="Tipos restrictivos — presión sobre valoración y crédito",
                    )
                )
                risks.append(f"Fed funds {fed:.2f}%: entorno restrictivo.")
                score -= 2.0
            elif fed <= 2.5:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="FED_FUNDS",
                        value=fed,
                        date=_date_of(fred.get("FED_FUNDS")),
                        signal="bullish",
                        note="Tipos acomodaticios — soporte a riesgo",
                    )
                )
                opportunities.append(f"Fed funds {fed:.2f}%: sesgo dovish.")
                score += 1.5
            else:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="FED_FUNDS",
                        value=fed,
                        signal="neutral",
                        note="Política monetaria intermedia",
                    )
                )

        # --- Inflation ---
        if cpi is not None:
            if cpi > 4.0:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="CPI_YOY", value=cpi, signal="bearish", note="Inflación elevada"
                    )
                )
                risks.append(f"CPI {cpi:.1f}%: Fed puede mantener tipos altos.")
                score -= 2.5
            elif cpi < 2.5:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="CPI_YOY", value=cpi, signal="bullish", note="Inflación cerca del objetivo"
                    )
                )
                opportunities.append(f"CPI {cpi:.1f}% cerca del target Fed.")
                score += 1.5
            else:
                indicators.append(
                    MacroIndicatorSnapshot(name="CPI_YOY", value=cpi, signal="neutral")
                )

        # --- Labor ---
        if unemp is not None:
            if unemp >= 5.0:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="UNEMPLOYMENT",
                        value=unemp,
                        signal="bearish",
                        note="Debilidad laboral — riesgo de recesión",
                    )
                )
                risks.append(f"Desempleo {unemp:.1f}%: deterioro cíclico.")
                score -= 2.0
            elif unemp <= 3.8:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="UNEMPLOYMENT",
                        value=unemp,
                        signal="bullish",
                        note="Mercado laboral fuerte",
                    )
                )
                score += 0.5
            else:
                indicators.append(
                    MacroIndicatorSnapshot(name="UNEMPLOYMENT", value=unemp, signal="neutral")
                )

        # --- Yield curve ---
        if curve is not None:
            if curve < 0:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="YIELD_CURVE_10Y2Y",
                        value=curve,
                        signal="bearish",
                        note="Curva invertida — señal históricamente recesiva",
                    )
                )
                risks.append(f"Curva 10A-2A invertida ({curve:.2f}%).")
                score -= 2.5
            elif curve > 0.5:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="YIELD_CURVE_10Y2Y",
                        value=curve,
                        signal="bullish",
                        note="Curva normalizada",
                    )
                )
                opportunities.append("Curva de rendimientos normalizada.")
                score += 1.0
            else:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="YIELD_CURVE_10Y2Y", value=curve, signal="neutral"
                    )
                )

        # --- VIX ---
        if vix is not None:
            if vix >= 30:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="VIX", value=vix, signal="bearish", note="Miedo extremo"
                    )
                )
                risks.append(f"VIX {vix:.1f}: estrés de volatilidad.")
                score -= 4.0
            elif vix >= 22:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="VIX", value=vix, signal="bearish", note="Volatilidad elevada"
                    )
                )
                risks.append(f"VIX {vix:.1f}: reducir tamaño.")
                score -= 2.0
            elif vix <= 14:
                indicators.append(
                    MacroIndicatorSnapshot(
                        name="VIX", value=vix, signal="bullish", note="Complacencia / baja vol"
                    )
                )
                score += 1.0
            else:
                indicators.append(MacroIndicatorSnapshot(name="VIX", value=vix, signal="neutral"))

        # Price-action regime overlay
        mr = (market_regime or "neutral").lower()
        if mr == "bullish":
            score += 1.5
        elif mr == "bearish":
            score -= 1.5

        mode, size_mult, cash_target = self._mode_from_score(score)
        macro_bias = self._macro_bias(fed, cpi, curve, unemp)
        trading_allowed = mode != "crisis"
        block_reason = None
        if mode == "crisis":
            block_reason = (
                "Régimen crisis (macro + volatilidad): nuevas compras bloqueadas. "
                "Preservar capital y esperar estabilización."
            )
            trading_allowed = False

        thesis = self._thesis(mode, mr, macro_bias, score, risks, opportunities)

        return MacroRegimeAssessment(
            mode=mode,
            market_regime=mr,
            macro_bias=macro_bias,
            score=round(score, 2),
            size_multiplier=size_mult,
            cash_target_pct=cash_target,
            vix=vix,
            fed_funds=fed,
            yield_curve_10y2y=curve,
            cpi_yoy=cpi,
            unemployment=unemp,
            indicators=indicators,
            thesis=thesis,
            risks=risks,
            opportunities=opportunities,
            trading_allowed=trading_allowed,
            block_reason=block_reason,
        )

    def _mode_from_score(self, score: float) -> tuple[RiskMode, float, float]:
        if score <= -6:
            return "crisis", 0.0, 60.0
        if score <= -2.5:
            return "risk_off", 0.35, 35.0
        if score >= 2.5:
            return "risk_on", 1.15, 8.0
        return "neutral", 1.0, 15.0

    def _macro_bias(
        self,
        fed: float | None,
        cpi: float | None,
        curve: float | None,
        unemp: float | None,
    ) -> str:
        if (unemp is not None and unemp >= 5.0) or (curve is not None and curve < -0.5):
            return "recessionary"
        if (fed is not None and fed >= 5.0) or (cpi is not None and cpi > 4.0):
            return "hawkish"
        if (fed is not None and fed <= 2.5) or (cpi is not None and cpi < 2.5):
            return "dovish"
        return "neutral"

    def _thesis(
        self,
        mode: RiskMode,
        market_regime: str,
        macro_bias: str,
        score: float,
        risks: list[str],
        opportunities: list[str],
    ) -> str:
        mode_es = {
            "risk_on": "riesgo-on (ampliar exposición con disciplina)",
            "neutral": "neutral (balancear alpha y cash)",
            "risk_off": "riesgo-off (reducir tamaño, subir cash)",
            "crisis": "crisis (bloquear compras, preservar capital)",
        }.get(mode, mode)
        parts = [
            f"Escritorio macro: modo {mode_es}.",
            f"Precio de mercado: {market_regime}; sesgo macro: {macro_bias}; score {score:+.1f}.",
        ]
        if risks:
            parts.append("Riesgos: " + "; ".join(risks[:3]))
        if opportunities:
            parts.append("Oportunidades: " + "; ".join(opportunities[:2]))
        return " ".join(parts)
