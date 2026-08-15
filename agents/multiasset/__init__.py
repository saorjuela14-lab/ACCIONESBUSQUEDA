"""Specialized agents for multi-asset beta desks."""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from agents.base import BaseAgent
from domain.enums import EvidenceCategory, ImpactLevel, TimeHorizon
from domain.reports import AgentReport, Finding
from providers.market.factory import get_market_provider
from utils.logging import get_logger

logger = get_logger(__name__)


def _yf_history(symbol: str, period: str = "3mo") -> pd.DataFrame:
    try:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period=period)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as exc:
        logger.warning("multiasset.yf_history_failed", symbol=symbol, error=str(exc))
        return pd.DataFrame()


def _change_pct(df: pd.DataFrame, bars: int = 5) -> float | None:
    if df.empty or "Close" not in df.columns or len(df) <= bars:
        return None
    c = df["Close"]
    return float((c.iloc[-1] / c.iloc[-1 - bars] - 1.0) * 100.0)


def _rsi(series: pd.Series, n: int = 14) -> float | None:
    if series is None or len(series) < n + 2:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else None


def _vol_ratio(df: pd.DataFrame) -> float | None:
    if df.empty or "Volume" not in df.columns or len(df) < 20:
        return None
    v = df["Volume"]
    avg = float(v.tail(20).mean())
    last = float(v.iloc[-1])
    return (last / avg) if avg > 0 else None


class _HeuristicAgent(BaseAgent):
    label_es: str = "agente"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        raise NotImplementedError

    def _report(
        self,
        ticker: str,
        score: float,
        confidence: float,
        summary: str,
        *,
        findings: list[Finding] | None = None,
        risks: list[Finding] | None = None,
        opportunities: list[Finding] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> AgentReport:
        return AgentReport(
            agent_name=self.name,
            ticker=ticker.upper(),
            score=self._clamp_score(score),
            confidence=self._clamp_confidence(confidence),
            summary=summary,
            findings=findings or [],
            risks=risks or [],
            opportunities=opportunities or [],
            raw_data=raw or {},
        )


# ─── GOLD ───────────────────────────────────────────────────────────────────


class GoldMacroAgent(_HeuristicAgent):
    name = "gold_macro_agent"
    label_es = "Oro · Macro"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        dxy, tlt, vix = await asyncio.gather(
            asyncio.to_thread(_yf_history, "DX-Y.NYB", "3mo"),
            asyncio.to_thread(_yf_history, "TLT", "3mo"),
            asyncio.to_thread(_yf_history, "^VIX", "3mo"),
        )
        dxy_5 = _change_pct(dxy, 5)
        tlt_5 = _change_pct(tlt, 5)
        vix_5 = _change_pct(vix, 5)
        score = 0.0
        bits: list[str] = []
        if dxy_5 is not None:
            # weaker USD → gold-friendly
            score += max(-25, min(25, -dxy_5 * 3))
            bits.append(f"DXY 5d {dxy_5:+.1f}%")
        if tlt_5 is not None:
            score += max(-15, min(15, tlt_5 * 1.5))
            bits.append(f"TLT 5d {tlt_5:+.1f}%")
        if vix_5 is not None and vix_5 > 8:
            score += 12
            bits.append(f"VIX sube {vix_5:+.1f}% (refugio)")
        summary = "Macro oro: " + ("; ".join(bits) if bits else "sin proxies macro")
        return self._report(
            ticker,
            score,
            0.55 if bits else 0.35,
            summary,
            findings=[
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=summary,
                    confidence=0.55,
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.WEEKLY,
                )
            ],
            raw={"dxy_5d": dxy_5, "tlt_5d": tlt_5, "vix_5d": vix_5},
        )


class GoldTechnicalAgent(_HeuristicAgent):
    name = "gold_technical_agent"
    label_es = "Oro · Técnico"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_yf_history, ticker, "6mo")
        chg = _change_pct(df, 10)
        rsi = _rsi(df["Close"]) if not df.empty else None
        score = 0.0
        bits = []
        if chg is not None:
            score += max(-30, min(30, chg * 2))
            bits.append(f"10d {chg:+.1f}%")
        if rsi is not None:
            if rsi < 35:
                score += 15
                bits.append(f"RSI {rsi:.0f} sobreventa")
            elif rsi > 70:
                score -= 15
                bits.append(f"RSI {rsi:.0f} sobrecompra")
            else:
                bits.append(f"RSI {rsi:.0f}")
        return self._report(
            ticker,
            score,
            0.6 if bits else 0.3,
            "Técnico oro ETF: " + ("; ".join(bits) or "sin datos"),
            raw={"change_10d": chg, "rsi": rsi},
        )


class GoldFlowAgent(_HeuristicAgent):
    name = "gold_flow_agent"
    label_es = "Oro · Flujo"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_yf_history, ticker, "3mo")
        rvol = _vol_ratio(df)
        chg = _change_pct(df, 1)
        score = 0.0
        bits = []
        if rvol is not None:
            if rvol >= 1.4 and chg is not None and chg > 0:
                score += 18
                bits.append(f"RVOL {rvol:.1f}× con alza")
            elif rvol >= 1.4 and chg is not None and chg < 0:
                score -= 12
                bits.append(f"RVOL {rvol:.1f}× con baja")
            else:
                bits.append(f"RVOL {rvol:.1f}×")
        return self._report(
            ticker,
            score,
            0.5 if bits else 0.3,
            "Flujo ETF oro: " + ("; ".join(bits) or "volumen normal"),
            raw={"rel_volume": rvol, "change_1d": chg},
        )


# ─── FOREX (ETF proxies) ────────────────────────────────────────────────────


class FxMacroAgent(_HeuristicAgent):
    name = "fx_macro_agent"
    label_es = "FX · Macro"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        # Relative rate / risk proxies via TLT and equity risk (SPY)
        tlt, spy = await asyncio.gather(
            asyncio.to_thread(_yf_history, "TLT", "3mo"),
            asyncio.to_thread(_yf_history, "SPY", "3mo"),
        )
        tlt_5 = _change_pct(tlt, 5)
        spy_5 = _change_pct(spy, 5)
        score = 0.0
        bits = []
        sym = ticker.upper()
        if sym == "UUP":
            # stronger rates / risk-off often supports USD
            if tlt_5 is not None:
                score += max(-20, min(20, -tlt_5 * 2))
                bits.append(f"TLT 5d {tlt_5:+.1f}%")
            if spy_5 is not None and spy_5 < -2:
                score += 10
                bits.append("risk-off favorece USD")
        else:
            # FXE/FXB/FXY — inverse-ish to USD strength
            if tlt_5 is not None:
                score += max(-20, min(20, tlt_5 * 1.5))
                bits.append(f"TLT 5d {tlt_5:+.1f}%")
        return self._report(
            ticker,
            score,
            0.5 if bits else 0.35,
            f"Macro FX proxy {sym}: " + ("; ".join(bits) or "sin señal"),
            raw={"tlt_5d": tlt_5, "spy_5d": spy_5},
        )


class FxTechnicalAgent(_HeuristicAgent):
    name = "fx_technical_agent"
    label_es = "FX · Técnico"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_yf_history, ticker, "6mo")
        chg = _change_pct(df, 5)
        rsi = _rsi(df["Close"]) if not df.empty else None
        score = 0.0
        bits = []
        if chg is not None:
            score += max(-25, min(25, chg * 3))
            bits.append(f"5d {chg:+.1f}%")
        if rsi is not None:
            bits.append(f"RSI {rsi:.0f}")
            if rsi < 30:
                score += 12
            elif rsi > 70:
                score -= 12
        return self._report(
            ticker,
            score,
            0.55 if bits else 0.3,
            "Técnico FX ETF: " + ("; ".join(bits) or "sin datos"),
            raw={"change_5d": chg, "rsi": rsi},
        )


class FxRiskAgent(_HeuristicAgent):
    name = "fx_risk_agent"
    label_es = "FX · Riesgo"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        df = await asyncio.to_thread(_yf_history, ticker, "3mo")
        if df.empty or len(df) < 20:
            return self._report(ticker, 0, 0.3, "Riesgo FX: histórico insuficiente")
        rets = df["Close"].pct_change().dropna()
        vol = float(rets.tail(20).std() * (252**0.5) * 100)
        score = 0.0
        risk_note = f"Vol anualizada ~{vol:.1f}%"
        if vol > 18:
            score -= 15
            risk_note += " (elevada para FX proxy)"
        elif vol < 8:
            score += 5
        return self._report(
            ticker,
            score,
            0.5,
            f"Riesgo FX: {risk_note}",
            risks=[
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=risk_note,
                    confidence=0.55,
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.WEEKLY,
                )
            ],
            raw={"ann_vol_pct": vol},
        )


# ─── CRYPTO ─────────────────────────────────────────────────────────────────


class CryptoMomentumAgent(_HeuristicAgent):
    name = "crypto_momentum_agent"
    label_es = "Crypto · Momentum"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        # yfinance wants BTC-USD
        ysym = ticker.replace("/", "-")
        df = await asyncio.to_thread(_yf_history, ysym, "3mo")
        c1 = _change_pct(df, 1)
        c7 = _change_pct(df, 7)
        score = 0.0
        bits = []
        if c1 is not None:
            score += max(-20, min(20, c1 * 2))
            bits.append(f"1d {c1:+.1f}%")
        if c7 is not None:
            score += max(-30, min(30, c7 * 1.5))
            bits.append(f"7d {c7:+.1f}%")
        return self._report(
            ticker,
            score,
            0.55 if bits else 0.3,
            "Momentum crypto: " + ("; ".join(bits) or "sin datos"),
            raw={"change_1d": c1, "change_7d": c7},
        )


class CryptoSentimentAgent(_HeuristicAgent):
    name = "crypto_sentiment_agent"
    label_es = "Crypto · Sentimiento"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        ysym = ticker.replace("/", "-")
        df = await asyncio.to_thread(_yf_history, ysym, "1mo")
        rvol = _vol_ratio(df)
        c1 = _change_pct(df, 1)
        score = 0.0
        bits = []
        if rvol is not None and c1 is not None:
            if rvol > 1.5 and c1 > 0:
                score += 20
                bits.append(f"volumen alto + alza (FOMO controlado)")
            elif rvol > 1.5 and c1 < -3:
                score -= 10
                bits.append("capitulación de corto plazo posible")
            else:
                bits.append(f"RVOL {rvol:.1f}×")
        return self._report(
            ticker,
            score,
            0.45 if bits else 0.3,
            "Sentimiento crypto: " + ("; ".join(bits) or "neutro"),
            raw={"rel_volume": rvol, "change_1d": c1},
        )


class CryptoRiskAgent(_HeuristicAgent):
    name = "crypto_risk_agent"
    label_es = "Crypto · Riesgo"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        ysym = ticker.replace("/", "-")
        df = await asyncio.to_thread(_yf_history, ysym, "3mo")
        if df.empty or len(df) < 15:
            return self._report(ticker, -10, 0.3, "Riesgo crypto: datos insuficientes — tamaño pequeño")
        rets = df["Close"].pct_change().dropna()
        vol = float(rets.tail(14).std() * (365**0.5) * 100)
        score = -min(40, vol / 3)  # higher vol → more cautious
        return self._report(
            ticker,
            score,
            0.55,
            f"Riesgo crypto: vol ~{vol:.0f}% anualizada — disciplina de stop amplia",
            risks=[
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"Volatilidad elevada (~{vol:.0f}% ann.)",
                    confidence=0.6,
                    impact=ImpactLevel.HIGH,
                    horizon=TimeHorizon.INTRADAY,
                )
            ],
            raw={"ann_vol_pct": vol},
        )


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "gold_macro_agent": GoldMacroAgent,
    "gold_technical_agent": GoldTechnicalAgent,
    "gold_flow_agent": GoldFlowAgent,
    "fx_macro_agent": FxMacroAgent,
    "fx_technical_agent": FxTechnicalAgent,
    "fx_risk_agent": FxRiskAgent,
    "crypto_momentum_agent": CryptoMomentumAgent,
    "crypto_sentiment_agent": CryptoSentimentAgent,
    "crypto_risk_agent": CryptoRiskAgent,
}


def build_agents(names: list[str]) -> list[BaseAgent]:
    out: list[BaseAgent] = []
    for n in names:
        cls = AGENT_REGISTRY.get(n)
        if cls:
            out.append(cls())
    return out


async def quote_symbol(symbol: str) -> dict[str, Any]:
    """Best-effort quote via composite market (ETFs) or yfinance (crypto)."""
    sym = symbol.upper()
    if "/" in sym or sym.endswith("USD") and len(sym) <= 7:
        ysym = sym.replace("/", "-")
        if "-" not in ysym and ysym.endswith("USD"):
            ysym = ysym[:-3] + "-USD"
        try:
            import yfinance as yf

            t = await asyncio.to_thread(lambda: yf.Ticker(ysym).fast_info)
            price = getattr(t, "last_price", None) or getattr(t, "lastPrice", None)
            return {"symbol": sym, "current_price": float(price) if price else None, "source": "yfinance"}
        except Exception:
            hist = await asyncio.to_thread(_yf_history, ysym, "5d")
            px = float(hist["Close"].iloc[-1]) if not hist.empty else None
            return {"symbol": sym, "current_price": px, "source": "yfinance"}
    try:
        q = await get_market_provider().get_quote(sym)
        return {
            "symbol": sym,
            "current_price": q.get("current_price") or q.get("price"),
            "company_name": q.get("company_name"),
            "source": "composite",
        }
    except Exception as exc:
        return {"symbol": sym, "current_price": None, "error": str(exc)}
