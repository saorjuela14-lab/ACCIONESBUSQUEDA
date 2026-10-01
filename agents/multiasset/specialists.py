"""Result-focused specialists (one per class). Paper only."""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding
from services.multiasset.signals import new_crypto_signal, new_forex_signal, new_gold_signal
from utils.logging import get_logger

logger = get_logger(__name__)


def _yf_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    try:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period=period)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as exc:
        logger.warning("specialist.yf_failed", symbol=symbol, error=str(exc))
        return pd.DataFrame()


class _SpecBase(BaseAgent):
    label_es = "especialista"

    def _report(self, ticker: str, score: float, confidence: float, summary: str, **kwargs) -> AgentReport:
        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(confidence),
            summary=summary,
            findings=kwargs.get("findings") or [],
            risks=kwargs.get("risks") or [],
            opportunities=kwargs.get("opportunities") or [],
            raw_data=kwargs.get("raw") or {},
        )


def _frame(ticker: str, period: str = "1y") -> pd.DataFrame:
    ysym = ticker.replace("/", "-")
    if "-" not in ysym and ysym.endswith("USD") and len(ysym) > 3:
        ysym = ysym[:-3] + "-USD"
    return _yf_history(ysym, period)


class GoldTrendSpecialist(_SpecBase):
    name = "gold_trend_specialist"
    label_es = "Oro · Especialista tendencia"
    objective_es = "CAGR positivo en OOS con DD acotado; expectancy ≥ 0 y PF ≥ 1.1 en GLD"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df, dxy = await asyncio.gather(
            asyncio.to_thread(_frame, ticker, "1y"),
            asyncio.to_thread(_yf_history, "DX-Y.NYB", "1y"),
        )
        if df.empty or len(df) < 60:
            return self._report(ticker, 0, 0.25, "Especialista oro: histórico insuficiente")
        dxy_10d = None
        if not dxy.empty and "Close" in dxy.columns and len(dxy) > 10:
            dxy_10d = float(dxy["Close"].iloc[-1] / dxy["Close"].iloc[-11] - 1.0)
        sig = new_gold_signal(df, len(df) - 1, dxy_10d=dxy_10d)
        score = 28 if sig.side == "buy" else (-18 if sig.side == "sell" else 0)
        last = float(df["Close"].iloc[-1])
        stop = round(last * (1 - sig.stop_pct), 4)
        return self._report(
            ticker,
            score,
            0.62 if sig.side == "buy" else 0.45,
            f"Especialista oro: {sig.reason} · stop {sig.stop_pct:.1%} (1R) trail {sig.trail_atr_mult}×ATR",
            opportunities=[
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=sig.reason,
                    confidence=0.6,
                    impact=ImpactLevel.HIGH,
                    horizon=TimeHorizon.WEEKLY,
                )
            ]
            if sig.side == "buy"
            else [],
            raw={
                "specialist": True,
                "setup": sig.side,
                "stop_pct": sig.stop_pct,
                "stop_r": 1.0,
                "stop_px": stop,
                "trail_atr_mult": sig.trail_atr_mult,
                "atr": sig.atr_abs,
                "leverage": 1.0,
                "objective": self.objective_es,
                **sig.extras,
            },
        )


class ForexMomentumSpecialist(_SpecBase):
    name = "fx_momentum_specialist"
    label_es = "FX · Especialista momentum/carry-lite"
    objective_es = "TSMOM en proxies ETF (UUP/FXE/FXB/FXY); PF ≥ 1.1 OOS, 1x"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_frame, ticker, "1y")
        if df.empty or len(df) < 80:
            return self._report(ticker, 0, 0.25, "Especialista FX: histórico insuficiente")
        sig = new_forex_signal(df, len(df) - 1)
        score = 26 if sig.side == "buy" else (-16 if sig.side == "sell" else 0)
        last = float(df["Close"].iloc[-1])
        stop = round(last * (1 - sig.stop_pct), 4)
        return self._report(
            ticker,
            score,
            0.58 if sig.side == "buy" else 0.42,
            f"Especialista FX: {sig.reason} · stop {sig.stop_pct:.1%} (1R)",
            raw={
                "specialist": True,
                "setup": sig.side,
                "stop_pct": sig.stop_pct,
                "stop_r": 1.0,
                "stop_px": stop,
                "trail_atr_mult": sig.trail_atr_mult,
                "atr": sig.atr_abs,
                "leverage": 1.0,
                "objective": self.objective_es,
                **sig.extras,
            },
        )


class CryptoBreakoutSpecialist(_SpecBase):
    name = "crypto_breakout_specialist"
    label_es = "Crypto · Especialista breakout 24/7"
    objective_es = "Breakout Donchian con trail; monitor 24/7 + stop GTC en broker"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_frame, ticker, "1y")
        if df.empty or len(df) < 60:
            return self._report(ticker, 0, 0.25, "Especialista crypto: histórico insuficiente")
        sig = new_crypto_signal(df, len(df) - 1)
        score = 32 if sig.side == "buy" else (-20 if sig.side == "sell" else 0)
        last = float(df["Close"].iloc[-1])
        stop = round(last * (1 - sig.stop_pct), 4)
        return self._report(
            ticker,
            score,
            0.6 if sig.side == "buy" else 0.4,
            f"Especialista crypto: {sig.reason} · stop {sig.stop_pct:.1%} (1R) · GTC 24/7",
            raw={
                "specialist": True,
                "setup": sig.side,
                "stop_pct": sig.stop_pct,
                "stop_r": 1.0,
                "stop_px": stop,
                "trail_atr_mult": sig.trail_atr_mult,
                "atr": sig.atr_abs,
                "leverage": 1.0,
                "crypto_24_7": True,
                "broker_stop": "gtc",
                "objective": self.objective_es,
                **sig.extras,
            },
        )


class CryptoStrategyASpecialist(_SpecBase):
    name = "crypto_strategy_a"
    label_es = "Crypto · Estrategia A (Donchian 4h)"
    objective_es = "Tendencia multi-horizonte 4h, solo compras; params fijos BTC/ETH; stops por software"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        from services.multiasset.strategy_a import ATR_STOP_MULT, signal_for_symbol

        frames = kwargs.get("frames")
        sig = await signal_for_symbol(ticker, frames=frames)
        score = 28 if sig.side == "buy" else 0
        return self._report(
            ticker,
            score,
            0.58 if sig.side == "buy" else 0.35,
            f"Estrategia A: {sig.reason}",
            raw={
                "specialist": True,
                "strategy": "A",
                "setup": sig.side,
                "stop_px": sig.stop_px,
                "atr": sig.atr_abs,
                "stop_mult": ATR_STOP_MULT,
                "software_stop": True,
                "leverage": 1.0,
                **sig.extras,
            },
        )


class DeskDirectorAgent(_SpecBase):
    """Shown on the board; allocation itself is services.multiasset.allocator."""

    name = "desk_director_agent"
    label_es = "Director de mesa"
    objective_es = "Asignar sleeve paper según régimen y PF/expectancy; recortar perdedores"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        from services.multiasset.allocator import allocate

        plan = allocate(market_open=kwargs.get("market_open"))
        weights = plan.get("weights") or {}
        summary = "Director: " + ", ".join(f"{k} {v:.0%}" for k, v in weights.items())
        if plan.get("notes"):
            summary += " · " + "; ".join(plan["notes"][:2])
        return self._report(
            ticker,
            0,
            0.5,
            summary,
            raw={**plan, "specialist": False, "director": True, "leverage": 1.0},
        )
