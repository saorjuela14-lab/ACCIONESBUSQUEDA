"""Multi-timeframe technical analysis agent — runs after prior agents with full context."""

import asyncio

import numpy as np

from agents.base import BaseAgent
from agents.technical.confluence import score_confluence
from agents.technical.context import (
    build_prior_context,
    correlate_technical_with_context,
)
from agents.technical.gaps import detect_gaps, resample_ohlc
from agents.technical.historical_setups import evaluate_historical_setups
from agents.technical.indicators import (
    build_trade_levels,
    detect_support_resistance,
    enrich_indicators,
)
from agents.technical.playbook import build_playbook
from agents.technical.structure import classify_structure
from agents.technical.volume_analysis import analyze_volume
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider
from utils.narrative_es import bias_label

TIMEFRAMES = [
    ("5m", "5d", "5m", None),
    ("15m", "5d", "15m", None),
    ("30m", "1mo", "30m", None),
    ("1H", "1mo", "1h", None),
    ("4H", "3mo", "1h", "4h"),
    ("1D", "1y", "1d", None),
    ("1W", "5y", "1wk", None),
    ("1M", "10y", "1mo", None),
]


class TechnicalAgent(BaseAgent):
    name = "technical_agent"

    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        prior_reports: list[AgentReport] = kwargs.get("prior_reports") or []
        prior_ctx = build_prior_context(prior_reports) if prior_reports else None

        quote = await self._market.get_quote(ticker)
        price = float(quote.get("current_price") or 0)

        timeframe_results: dict[str, dict] = {}
        tasks = [
            self._analyze_timeframe(ticker, label, period, interval, resample)
            for label, period, interval, resample in TIMEFRAMES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (label, _, _, _), result in zip(TIMEFRAMES, results):
            if isinstance(result, Exception):
                continue
            timeframe_results[label] = result

        daily = timeframe_results.get("1D", {})
        support = daily.get("support", price * 0.95)
        resistance = daily.get("resistance", price * 1.05)
        atr = daily.get("atr", price * 0.02) or price * 0.02
        trade_levels = build_trade_levels(price, support, resistance, atr)

        structure = daily.get("structure") or {
            "structure": "range",
            "label_es": "sin datos diarios",
            "confidence": 0.3,
        }
        volume = daily.get("volume") or {}
        historical = daily.get("historical") or {"available": False, "setups": {}}
        confluence = score_confluence(timeframe_results)

        unfilled_gap_count = 0
        gap_findings: list[Finding] = []
        for tf in ("1D", "1W", "4H"):
            gaps = (timeframe_results.get(tf) or {}).get("gaps") or []
            open_gaps = [g for g in gaps if not g.get("filled")]
            unfilled_gap_count += len(open_gaps)
            for g in open_gaps[:2]:
                gap_findings.append(
                    Finding(
                        category=EvidenceCategory.FACT,
                        statement=(
                            f"Gap {tf} sin cubrir {g.get('gap_type')}: "
                            f"${g.get('gap_bottom')}–${g.get('gap_top')} "
                            f"(fill → ${g.get('fill_target')})"
                        ),
                        confidence=0.6,
                        references=[Reference(source="technical", data_point=f"gap_{tf}", value=g.get("gap_size_pct"))],
                        horizon=self._horizon_for(tf),
                    )
                )

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []

        # HTF-weighted score instead of flat average
        weighted = 0.0
        w_sum = 0.0
        from agents.technical.confluence import TF_WEIGHTS

        for tf, data in timeframe_results.items():
            if not data:
                continue
            tf_score = float(data.get("score", 0))
            w = TF_WEIGHTS.get(tf, 1.0)
            weighted += tf_score * w
            w_sum += w
            ref = Reference(source="yfinance", data_point=f"{tf}_score", value=tf_score)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"{tf}: {bias_label(data.get('bias', 'neutral'))} (score {tf_score:+.1f})",
                    confidence=data.get("confidence", 0.5),
                    references=[ref],
                    horizon=self._horizon_for(tf),
                )
            )

        avg_score = weighted / w_sum if w_sum else 0.0
        # Confluence boost/penalty
        conf_score = float(confluence.get("score") or 0) / 100.0
        avg_score = avg_score + conf_score * 1.5
        if confluence.get("aligned_with_htf"):
            avg_score += 0.8 if confluence.get("htf_bias") == "bullish" else -0.8 if confluence.get("htf_bias") == "bearish" else 0

        raw_technical_score = self._clamp_score(avg_score * 10)
        valid_frames = sum(1 for d in timeframe_results.values() if d)
        base_confidence = self._clamp_confidence(
            0.35 + valid_frames * 0.05 + (0.08 if confluence.get("aligned_with_htf") else 0)
        )

        findings.append(
            Finding(
                category=EvidenceCategory.INTERPRETATION,
                statement=(
                    f"Estructura {structure.get('label_es')}; "
                    f"confluencia {confluence.get('label_es')} "
                    f"(acuerdo {confluence.get('agreement_pct')}%)"
                ),
                confidence=float(structure.get("confidence") or 0.5),
                references=[],
            )
        )
        findings.extend(gap_findings)

        context_result = None
        final_score = raw_technical_score
        final_confidence = base_confidence
        if prior_ctx and prior_reports:
            context_result = correlate_technical_with_context(
                technical_score=raw_technical_score,
                daily_bias=daily.get("bias", "neutral"),
                daily_rsi=daily.get("rsi"),
                ctx=prior_ctx,
            )
            final_score = self._clamp_score(raw_technical_score + context_result.score_adjustment)
            final_confidence = self._clamp_confidence(
                base_confidence + context_result.confidence_adjustment
            )
            findings.extend(context_result.findings)
            risks.extend(context_result.risks)
            opportunities.extend(context_result.opportunities)

        if daily.get("rsi") is not None:
            rsi = daily["rsi"]
            if rsi < 30:
                opportunities.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement=f"RSI diario {rsi:.1f}: sobreventa — típico de rebotes tácticos si HTF no es bajista fuerte",
                        confidence=0.7,
                        references=[Reference(source="technical", data_point="RSI", value=rsi)],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            elif rsi > 70:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"RSI diario {rsi:.1f}: sobrecompra — riesgo de toma de beneficios",
                        confidence=0.7,
                        references=[Reference(source="technical", data_point="RSI", value=rsi)],
                        impact=ImpactLevel.MEDIUM,
                    )
                )

        if daily.get("adx") is not None and daily["adx"] >= 25:
            findings.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement=f"ADX {daily['adx']:.1f}: tendencia con fuerza (filtro >25)",
                    confidence=0.65,
                    references=[Reference(source="technical", data_point="ADX", value=daily["adx"])],
                )
            )

        if trade_levels.get("risk_reward_ratio"):
            findings.append(
                Finding(
                    category=EvidenceCategory.PROBABILITY,
                    statement=f"R/R estimado {trade_levels['risk_reward_ratio']}x (filtro profesional ≥1.5–2x)",
                    confidence=0.65,
                    references=[Reference(source="technical", data_point="risk_reward", value=trade_levels["risk_reward_ratio"])],
                )
            )

        committee_notes = context_result.correlation_notes if context_result else []
        playbook = build_playbook(
            ticker=ticker.upper(),
            price=price or float(daily.get("close") or 0),
            daily=daily,
            structure=structure,
            confluence=confluence,
            volume=volume,
            historical=historical,
            trade_levels=trade_levels,
            unfilled_gaps=unfilled_gap_count,
            committee_notes=committee_notes,
        )

        if playbook["opinion"] == "constructive":
            opportunities.append(
                Finding(
                    category=EvidenceCategory.OPINION,
                    statement=playbook["thesis"],
                    confidence=final_confidence,
                    references=[],
                    impact=ImpactLevel.MEDIUM,
                )
            )
        elif playbook["opinion"] == "defensive":
            risks.append(
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=playbook["thesis"],
                    confidence=final_confidence,
                    references=[],
                    impact=ImpactLevel.MEDIUM,
                )
            )

        context_summary = ""
        if prior_ctx and context_result:
            context_summary = (
                f" Contexto comité: {len(context_result.correlation_notes)} correlaciones; "
                f"fundamental {prior_ctx.fundamental_score:+.1f}, narrativa {prior_ctx.narrative_score:+.1f}, "
                f"macro {prior_ctx.macro_score:+.1f}."
            )

        summary = (
            f"{playbook['summary']} "
            f"Análisis en {valid_frames} horizontes "
            f"({'con' if prior_reports else 'sin'} contexto previo). "
            f"Soporte ${support:.2f}, resistencia ${resistance:.2f}."
            f"{context_summary}"
        )

        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=final_score,
            confidence=final_confidence,
            findings=findings,
            risks=risks,
            opportunities=opportunities,
            references=references,
            raw_data={
                "timeframes": timeframe_results,
                "trade_levels": trade_levels,
                "current_price": price,
                "raw_technical_score": raw_technical_score,
                "context_adjustment": context_result.score_adjustment if context_result else 0.0,
                "prior_agent_scores": prior_ctx.scores if prior_ctx else {},
                "cross_agent_correlations": context_result.correlation_notes if context_result else [],
                "prior_summaries": prior_ctx.summaries if prior_ctx else {},
                "structure": structure,
                "confluence": confluence,
                "volume": volume,
                "historical_setups": historical,
                "playbook": playbook,
                "unfilled_gap_count": unfilled_gap_count,
            },
            summary=summary,
        )

    async def _analyze_timeframe(
        self,
        ticker: str,
        label: str,
        period: str,
        interval: str,
        resample: str | None,
    ) -> dict:
        df = await self._market.get_history(ticker, period=period, interval=interval)
        if df.empty or len(df) < 30:
            return {}
        if resample:
            df = resample_ohlc(df, resample)
            if df.empty or len(df) < 20:
                return {}

        enriched = enrich_indicators(df)
        last = enriched.iloc[-1]
        prev = enriched.iloc[-2]
        score = 0.0

        rsi = float(last.get("RSI", np.nan))
        if not np.isnan(rsi):
            if rsi < 30:
                score += 2
            elif rsi > 70:
                score -= 2
            elif 40 <= rsi <= 60:
                score += 0.3  # healthy mid-range momentum zone

        macd = last.get("MACD", np.nan)
        macd_sig = last.get("MACD_Signal", np.nan)
        macd_hist = last.get("MACD_Hist", np.nan)
        if not np.isnan(macd) and not np.isnan(macd_sig):
            score += 1.5 if macd > macd_sig else -1.5
        if not np.isnan(macd_hist) and not np.isnan(prev.get("MACD_Hist", np.nan)):
            # Expanding histogram = momentum confirmation
            if macd_hist > prev["MACD_Hist"] and macd_hist > 0:
                score += 0.5
            elif macd_hist < prev["MACD_Hist"] and macd_hist < 0:
                score -= 0.5

        sma20 = last.get("SMA20", np.nan)
        sma50 = last.get("SMA50", np.nan)
        sma200 = last.get("SMA200", np.nan)
        close = float(last["Close"])
        if not np.isnan(sma20):
            score += 1 if close > sma20 else -1
        if not np.isnan(sma50):
            score += 1 if close > sma50 else -1
        if not np.isnan(sma200):
            score += 1.2 if close > sma200 else -1.2

        if not np.isnan(sma20) and not np.isnan(sma50):
            if prev.get("SMA20", 0) < prev.get("SMA50", 0) and sma20 > sma50:
                score += 3
            elif prev.get("SMA20", 0) > prev.get("SMA50", 0) and sma20 < sma50:
                score -= 3

        vwap = last.get("VWAP", np.nan)
        if not np.isnan(vwap):
            score += 0.4 if close > vwap else -0.4

        vol_sma = last.get("Volume_SMA20", np.nan)
        vol = float(last["Volume"]) if "Volume" in enriched.columns and pd_notna(last.get("Volume")) else None
        if vol is not None and not np.isnan(vol_sma) and vol_sma > 0:
            if vol >= vol_sma * 1.2 and score > 0:
                score += 0.6
            elif vol >= vol_sma * 1.2 and score < 0:
                score -= 0.6

        adx = float(last.get("ADX", np.nan))
        if not np.isnan(adx) and adx >= 25:
            score *= 1.1  # amplify when trend is strong

        levels = detect_support_resistance(enriched)
        structure = classify_structure(enriched)
        volume = analyze_volume(enriched)
        historical = evaluate_historical_setups(enriched) if label == "1D" else {}

        # Structure tilt
        if structure.get("structure") == "uptrend":
            score += 0.8
        elif structure.get("structure") == "downtrend":
            score -= 0.8

        bias = "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral"

        gaps_raw = []
        try:
            min_pct = {"1D": 0.25, "1W": 0.5, "4H": 0.15}.get(label, 0.2)
            detected = detect_gaps(enriched, timeframe=label, min_gap_pct=min_pct, interval=interval)
            gaps_raw = [g.model_dump() if hasattr(g, "model_dump") else g.dict() for g in detected[-8:]]
        except Exception:
            gaps_raw = []

        return {
            "score": round(score, 3),
            "bias": bias,
            "rsi": rsi if not np.isnan(rsi) else None,
            "adx": round(adx, 2) if not np.isnan(adx) else None,
            "atr": float(last.get("ATR", np.nan)) if not np.isnan(last.get("ATR", np.nan)) else None,
            "sma20": float(sma20) if not np.isnan(sma20) else None,
            "sma50": float(sma50) if not np.isnan(sma50) else None,
            "sma200": float(sma200) if not np.isnan(sma200) else None,
            "close": close,
            "confidence": 0.55 + min(len(enriched), 200) / 500,
            "structure": structure,
            "volume": volume,
            "historical": historical,
            "gaps": gaps_raw,
            **levels,
        }

    def _horizon_for(self, tf: str) -> TimeHorizon:
        mapping = {
            "5m": TimeHorizon.INTRADAY,
            "15m": TimeHorizon.INTRADAY,
            "30m": TimeHorizon.INTRADAY,
            "1H": TimeHorizon.INTRADAY,
            "4H": TimeHorizon.WEEKLY,
            "1D": TimeHorizon.MONTHLY,
            "1W": TimeHorizon.MONTHLY,
            "1M": TimeHorizon.LONG_TERM,
        }
        return mapping.get(tf, TimeHorizon.WEEKLY)


def pd_notna(val) -> bool:
    try:
        return bool(val == val) and val is not None
    except Exception:
        return False
