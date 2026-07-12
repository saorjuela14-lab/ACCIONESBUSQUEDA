"""Build OHLC + indicator series for dashboard charts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.technical.indicators import build_trade_levels, detect_support_resistance, enrich_indicators
from domain.dashboard import TechnicalChartData, TechnicalChartPoint, TechnicalSnapshot
from providers.interfaces import MarketDataProvider
from utils.logging import get_logger
from utils.narrative_es import bias_label

logger = get_logger(__name__)


class TechnicalChartService:
    def __init__(self, market_provider: MarketDataProvider) -> None:
        self._market = market_provider

    async def build(self, ticker: str, period: str = "6mo") -> TechnicalChartData:
        ticker = ticker.upper()
        df = await self._market.get_history(ticker, period=period, interval="1d")

        if df.empty or len(df) < 30:
            return TechnicalChartData(
                ticker=ticker,
                period=period,
                summary="Datos insuficientes para análisis técnico.",
            )

        enriched = enrich_indicators(df)
        levels = detect_support_resistance(enriched)
        last = enriched.iloc[-1]
        price = float(last["Close"])
        atr = float(last["ATR"]) if pd.notna(last.get("ATR")) else price * 0.02
        trade_levels = build_trade_levels(
            price,
            levels["support"],
            levels["resistance"],
            atr,
        )

        points: list[TechnicalChartPoint] = []
        for idx, row in enriched.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            vol = float(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else None
            points.append(
                TechnicalChartPoint(
                    date=date_str,
                    open=round(float(row["Open"]), 4),
                    high=round(float(row["High"]), 4),
                    low=round(float(row["Low"]), 4),
                    close=round(float(row["Close"]), 4),
                    volume=vol,
                    sma20=self._f(row.get("SMA20")),
                    sma50=self._f(row.get("SMA50")),
                    ema20=self._f(row.get("EMA20")),
                    bb_upper=self._f(row.get("BB_Upper")),
                    bb_lower=self._f(row.get("BB_Lower")),
                    rsi=self._f(row.get("RSI")),
                    macd=self._f(row.get("MACD")),
                    macd_signal=self._f(row.get("MACD_Signal")),
                    macd_hist=self._f(row.get("MACD_Hist")),
                )
            )

        rsi = self._f(last.get("RSI"))
        macd = self._f(last.get("MACD"))
        macd_sig = self._f(last.get("MACD_Signal"))
        sma20 = self._f(last.get("SMA20"))
        sma50 = self._f(last.get("SMA50"))

        bias = "neutral"
        score = 0.0
        if rsi is not None:
            if rsi < 30:
                score += 2
            elif rsi > 70:
                score -= 2
        if macd is not None and macd_sig is not None:
            score += 1.5 if macd > macd_sig else -1.5
        if sma20 is not None:
            score += 1 if price > sma20 else -1
        if sma50 is not None:
            score += 1 if price > sma50 else -1
        bias = "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral"

        snapshot = TechnicalSnapshot(
            price=round(price, 2),
            rsi=rsi,
            macd=macd,
            macd_signal=macd_sig,
            macd_hist=self._f(last.get("MACD_Hist")),
            sma20=sma20,
            sma50=sma50,
            ema20=self._f(last.get("EMA20")),
            atr=round(atr, 2),
            bias=bias,
            support=round(levels["support"], 2),
            resistance=round(levels["resistance"], 2),
            stop_loss=round(trade_levels.get("stop_loss", 0), 2) if trade_levels.get("stop_loss") else None,
            take_profit_1=round(trade_levels.get("take_profit_1", 0), 2) if trade_levels.get("take_profit_1") else None,
            risk_reward=trade_levels.get("risk_reward_ratio"),
        )

        summary = self._build_summary(ticker, snapshot, bias)

        return TechnicalChartData(
            ticker=ticker,
            period=period,
            points=points,
            snapshot=snapshot,
            trade_levels=trade_levels,
            summary=summary,
        )

    def _f(self, val) -> float | None:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        try:
            return round(float(val), 4)
        except (TypeError, ValueError):
            return None

    def _build_summary(self, ticker: str, snap: TechnicalSnapshot, bias: str) -> str:
        parts = [
            f"Análisis técnico diario de {ticker}: sesgo {bias_label(bias)}.",
            f"Precio ${snap.price}, RSI {snap.rsi or 'N/D'}, MACD {'alcista' if snap.macd and snap.macd_signal and snap.macd > snap.macd_signal else 'bajista' if snap.macd and snap.macd_signal else 'N/D'}.",
            f"SMA20 ${snap.sma20 or '—'}, SMA50 ${snap.sma50 or '—'}.",
            f"Soporte ${snap.support}, resistencia ${snap.resistance}.",
        ]
        if snap.stop_loss and snap.take_profit_1:
            parts.append(f"Stop ${snap.stop_loss}, objetivo ${snap.take_profit_1}" + (f" (R/R {snap.risk_reward}x)" if snap.risk_reward else "") + ".")
        return " ".join(parts)
