"""Multi-timeframe technical analysis agent — runs after prior agents with full context."""

import asyncio

import numpy as np

from agents.base import BaseAgent
from agents.technical.context import (
    build_prior_context,
    correlate_technical_with_context,
)
from agents.technical.indicators import (
    build_trade_levels,
    detect_support_resistance,
    enrich_indicators,
)
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding, Reference
from providers.interfaces import MarketDataProvider
from utils.narrative_es import bias_label

TIMEFRAMES = [
    ("5m", "5d", "5m"),
    ("15m", "5d", "15m"),
    ("30m", "1mo", "30m"),
    ("1H", "1mo", "1h"),
    ("4H", "3mo", "1h"),
    ("1D", "1y", "1d"),
    ("1W", "5y", "1wk"),
    ("1M", "10y", "1mo"),
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
            self._analyze_timeframe(ticker, label, period, interval)
            for label, period, interval in TIMEFRAMES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (label, _, _), result in zip(TIMEFRAMES, results):
            if isinstance(result, Exception):
                continue
            timeframe_results[label] = result

        daily = timeframe_results.get("1D", {})
        support = daily.get("support", price * 0.95)
        resistance = daily.get("resistance", price * 1.05)
        atr = daily.get("atr", price * 0.02)
        trade_levels = build_trade_levels(price, support, resistance, atr)

        findings: list[Finding] = []
        risks: list[Finding] = []
        opportunities: list[Finding] = []
        references: list[Reference] = []
        score = 0.0
        valid_frames = 0

        for tf, data in timeframe_results.items():
            if not data:
                continue
            valid_frames += 1
            tf_score = data.get("score", 0)
            score += tf_score
            ref = Reference(source="yfinance", data_point=f"{tf}_score", value=tf_score)
            references.append(ref)
            findings.append(
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=f"{tf}: {data.get('bias', 'neutral')} (score {tf_score:+.1f})",
                    confidence=data.get("confidence", 0.5),
                    references=[ref],
                    horizon=self._horizon_for(tf),
                )
            )

        avg_score = score / valid_frames if valid_frames else 0.0
        raw_technical_score = self._clamp_score(avg_score * 10)
        base_confidence = self._clamp_confidence(0.35 + valid_frames * 0.06)

        # Context-aware correlation layer (requires prior agent reports)
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
                        statement=f"Daily RSI {rsi:.1f} indicates oversold conditions",
                        confidence=0.7,
                        references=[Reference(source="technical", data_point="RSI", value=rsi)],
                        impact=ImpactLevel.MEDIUM,
                    )
                )
            elif rsi > 70:
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"Daily RSI {rsi:.1f} indicates overbought conditions",
                        confidence=0.7,
                        references=[Reference(source="technical", data_point="RSI", value=rsi)],
                        impact=ImpactLevel.MEDIUM,
                    )
                )

        if trade_levels.get("risk_reward_ratio"):
            findings.append(
                Finding(
                    category=EvidenceCategory.PROBABILITY,
                    statement=f"Risk/reward ratio estimated at {trade_levels['risk_reward_ratio']}x",
                    confidence=0.65,
                    references=[Reference(source="technical", data_point="risk_reward", value=trade_levels["risk_reward_ratio"])],
                )
            )

        context_summary = ""
        if prior_ctx and context_result:
            context_summary = (
                f" Context-informed: {len(context_result.correlation_notes)} cross-agent correlations; "
                f"fundamental {prior_ctx.fundamental_score:+.1f}, narrative {prior_ctx.narrative_score:+.1f}, "
                f"macro {prior_ctx.macro_score:+.1f}."
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
            },
            summary=(
                f"Análisis técnico multi-timeframe en {valid_frames} horizontes "
                f"({'con' if prior_reports else 'sin'} contexto previo del comité). "
                f"Sesgo diario: {bias_label(daily.get('bias', 'neutral'))}. "
                f"Soporte ${support:.2f}, Resistencia ${resistance:.2f}."
                f"{context_summary}"
            ),
        )

    async def _analyze_timeframe(self, ticker: str, label: str, period: str, interval: str) -> dict:
        df = await self._market.get_history(ticker, period=period, interval=interval)
        if df.empty or len(df) < 30:
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

        macd = last.get("MACD", np.nan)
        macd_sig = last.get("MACD_Signal", np.nan)
        if not np.isnan(macd) and not np.isnan(macd_sig):
            score += 1.5 if macd > macd_sig else -1.5

        sma20 = last.get("SMA20", np.nan)
        sma50 = last.get("SMA50", np.nan)
        close = float(last["Close"])
        if not np.isnan(sma20):
            score += 1 if close > sma20 else -1
        if not np.isnan(sma50):
            score += 1 if close > sma50 else -1

        if not np.isnan(sma20) and not np.isnan(sma50):
            if prev.get("SMA20", 0) < prev.get("SMA50", 0) and sma20 > sma50:
                score += 3
            elif prev.get("SMA20", 0) > prev.get("SMA50", 0) and sma20 < sma50:
                score -= 3

        levels = detect_support_resistance(enriched)
        bias = "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral"

        return {
            "score": score,
            "bias": bias,
            "rsi": rsi if not np.isnan(rsi) else None,
            "atr": float(last.get("ATR", np.nan)) if not np.isnan(last.get("ATR", np.nan)) else None,
            "confidence": 0.55 + min(len(enriched), 200) / 500,
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
