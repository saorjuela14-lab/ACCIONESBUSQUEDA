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


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _macd(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    if close is None or len(close) < 35:
        return None, None, None
    macd_line = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd_line, 9)
    hist = macd_line - signal
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def _bollinger(close: pd.Series, n: int = 20) -> tuple[float | None, float | None, float | None, float | None]:
    if close is None or len(close) < n + 2:
        return None, None, None, None
    mid = close.rolling(n).mean()
    std = close.rolling(n).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = float(((upper.iloc[-1] - lower.iloc[-1]) / mid.iloc[-1]) * 100) if mid.iloc[-1] else None
    return float(mid.iloc[-1]), float(upper.iloc[-1]), float(lower.iloc[-1]), width


def _crypto_social_ticker(symbol: str) -> str:
    """Map BTC/USD → BTC for Stocktwits / news search."""
    s = symbol.upper().replace(" ", "")
    if "/" in s:
        return s.split("/")[0]
    if s.endswith("USD") and len(s) > 3:
        return s[:-3]
    return s


class CryptoChartTechnicalAgent(_HeuristicAgent):
    """Lead chart agent — EMA trend + RSI regime + MACD timing + volume/BB confirmation.

    Stack inspired by common crypto swing practice:
    structure/EMA filter → RSI regime → MACD trigger → volume / Bollinger participation.
    """

    name = "crypto_chart_technical_agent"
    label_es = "Crypto · Técnico gráfico"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        ysym = ticker.replace("/", "-")
        df = await asyncio.to_thread(_yf_history, ysym, "6mo")
        if df.empty or "Close" not in df.columns or len(df) < 60:
            return self._report(ticker, 0, 0.25, "Técnico gráfico: histórico insuficiente")

        close = df["Close"]
        last = float(close.iloc[-1])
        ema20 = float(_ema(close, 20).iloc[-1])
        ema50 = float(_ema(close, 50).iloc[-1])
        ema200 = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else None
        rsi = _rsi(close)
        macd_v, signal_v, hist_v = _macd(close)
        bb_mid, bb_up, bb_lo, bb_width = _bollinger(close)
        rvol = _vol_ratio(df)
        high20 = float(close.tail(20).max())
        prev_high20 = float(close.iloc[-21:-1].max()) if len(close) > 21 else high20

        score = 0.0
        bits: list[str] = []
        opps: list[Finding] = []
        risks: list[Finding] = []
        setups: list[str] = []

        # 1) Trend filter (EMA stack)
        if ema200 is not None and last > ema200 and ema50 > ema200:
            score += 18
            bits.append("tendencia alcista (precio>EMA200, EMA50>EMA200)")
            setups.append("trend_up")
        elif ema200 is not None and last < ema200 and ema50 < ema200:
            score -= 18
            bits.append("tendencia bajista (bajo EMA200)")
            setups.append("trend_down")
        elif last > ema50 > ema20 * 0.98:
            score += 10
            bits.append("sesgo alcista EMA20/50")
            setups.append("ema_bias_up")
        else:
            bits.append("tendencia mixta / rango")

        # 2) Pullback-to-EMA20 in uptrend (classic crypto swing)
        if last > ema50 and ema20 > 0:
            dist_ema20 = (last - ema20) / ema20 * 100
            if -2.5 <= dist_ema20 <= 1.5 and rsi is not None and 40 <= rsi <= 55:
                score += 22
                setups.append("pullback_ema20")
                bits.append(f"pullback EMA20 (dist {dist_ema20:+.1f}%, RSI {rsi:.0f})")
                opps.append(
                    Finding(
                        category=EvidenceCategory.INTERPRETATION,
                        statement="Pullback a EMA20 en tendencia — setup swing clásico",
                        confidence=0.65,
                        impact=ImpactLevel.HIGH,
                        horizon=TimeHorizon.INTRADAY,
                    )
                )

        # 3) MACD timing
        if hist_v is not None and macd_v is not None and signal_v is not None:
            prev_hist = None
            try:
                macd_line = _ema(close, 12) - _ema(close, 26)
                sig = _ema(macd_line, 9)
                prev_hist = float((macd_line - sig).iloc[-2])
            except Exception:
                prev_hist = None
            if macd_v > signal_v and (prev_hist is None or hist_v > prev_hist):
                score += 16
                bits.append("MACD alcista / histograma expandiendo")
                setups.append("macd_bull")
            elif macd_v < signal_v:
                score -= 12
                bits.append("MACD bajista")
                setups.append("macd_bear")

        # 4) RSI regime (not chasing overbought)
        if rsi is not None:
            bits.append(f"RSI {rsi:.0f}")
            if rsi < 35:
                score += 14
                setups.append("rsi_oversold")
            elif 45 <= rsi <= 62:
                score += 8
                setups.append("rsi_healthy")
            elif rsi > 72:
                score -= 14
                setups.append("rsi_overbought")
                risks.append(
                    Finding(
                        category=EvidenceCategory.RISK,
                        statement=f"RSI {rsi:.0f} sobrecompra — evitar chase",
                        confidence=0.6,
                        impact=ImpactLevel.MEDIUM,
                        horizon=TimeHorizon.INTRADAY,
                    )
                )

        # 5) Breakout + volume
        breakout = last >= high20 * 0.998 and last > prev_high20
        if breakout and rvol is not None and rvol >= 1.4:
            score += 20
            setups.append("vol_breakout")
            bits.append(f"breakout 20d con RVOL {rvol:.1f}×")
            opps.append(
                Finding(
                    category=EvidenceCategory.FACT,
                    statement="Ruptura de máximos 20 sesiones con volumen",
                    confidence=0.7,
                    impact=ImpactLevel.HIGH,
                    horizon=TimeHorizon.INTRADAY,
                )
            )
        elif breakout and (rvol is None or rvol < 1.1):
            score -= 6
            bits.append("breakout sin volumen (posible trampa)")
            setups.append("weak_breakout")

        # 6) Bollinger squeeze → expansion bias
        if bb_width is not None:
            if bb_width < 6 and last > (bb_mid or last):
                score += 8
                bits.append(f"BB squeeze ({bb_width:.1f}%) sesgo arriba")
                setups.append("bb_squeeze_up")
            elif bb_lo is not None and last <= bb_lo * 1.01:
                score += 6
                bits.append("toque banda inferior BB")
                setups.append("bb_lower")

        conf = 0.45 + 0.08 * min(5, len(setups))
        summary = "Técnico gráfico: " + ("; ".join(bits) if bits else "sin señal clara")
        return self._report(
            ticker,
            score,
            min(0.85, conf),
            summary,
            findings=[
                Finding(
                    category=EvidenceCategory.INTERPRETATION,
                    statement=summary,
                    confidence=min(0.85, conf),
                    impact=ImpactLevel.HIGH,
                    horizon=TimeHorizon.INTRADAY,
                )
            ],
            risks=risks[:3],
            opportunities=opps[:3],
            raw={
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200,
                "rsi": rsi,
                "macd": macd_v,
                "macd_signal": signal_v,
                "macd_hist": hist_v,
                "rvol": rvol,
                "bb_width": bb_width,
                "setups": setups,
                "strategy": "ema_rsi_macd_volume_bb",
            },
        )


class CryptoMomentumAgent(_HeuristicAgent):
    name = "crypto_momentum_agent"
    label_es = "Crypto · Momentum"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
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


class CryptoNewsSocialAgent(_HeuristicAgent):
    """News + social perception (Stocktwits / Yahoo news) — crypto moves on narrative."""

    name = "crypto_news_social_agent"
    label_es = "Crypto · Noticias & redes"

    async def analyze(self, ticker: str, **kwargs) -> AgentReport:
        social = _crypto_social_ticker(ticker)
        score = 0.0
        bits: list[str] = []
        conf = 0.35
        raw: dict[str, Any] = {"social_ticker": social}

        # Stocktwits stream (BTC / ETH / SOL)
        try:
            from providers.sentiment.stocktwits_provider import StocktwitsProvider

            snap = await StocktwitsProvider().get_sentiment(social)
            st_score = float(snap.score or 0)
            score += max(-30, min(30, st_score * 0.8))
            bits.append(f"Stocktwits {st_score:+.0f} (n={snap.sample_size})")
            conf = max(conf, 0.5 if snap.sample_size else 0.35)
            raw["stocktwits"] = {"score": st_score, "n": snap.sample_size}
        except Exception as exc:
            logger.warning("crypto.social.stocktwits_failed", error=str(exc))
            bits.append("Stocktwits no disponible")

        # Yahoo / news headline sentiment
        try:
            from providers.sentiment.yfinance_news_sentiment_provider import (
                YFinanceNewsSentimentProvider,
            )

            news = await YFinanceNewsSentimentProvider().get_sentiment(social)
            n_score = float(news.score or 0)
            score += max(-25, min(25, n_score * 0.7))
            bits.append(f"News {n_score:+.0f} (n={news.sample_size})")
            conf = max(conf, 0.45 if news.sample_size else conf)
            raw["news"] = {"score": n_score, "n": news.sample_size}
            if news.items:
                raw["headlines"] = [i.text[:80] for i in news.items[:3]]
        except Exception as exc:
            logger.warning("crypto.social.news_failed", error=str(exc))

        # Light DDG narrative pulse (perception)
        try:
            from duckduckgo_search import DDGS

            q = f"{social} crypto news sentiment"
            results = await asyncio.to_thread(
                lambda: list(DDGS().text(q, max_results=5))
            )
            bull_kw = ("surge", "rally", "bull", "etf", "inflow", "breakout", "all-time", "approve")
            bear_kw = ("hack", "ban", "crash", "sec charge", "outflow", "bear", "lawsuit", "exploit")
            text = " ".join(
                f"{(r.get('title') or '')} {(r.get('body') or '')}".lower() for r in results
            )
            b = sum(1 for k in bull_kw if k in text)
            s = sum(1 for k in bear_kw if k in text)
            delta = (b - s) * 6
            score += max(-18, min(18, delta))
            bits.append(f"Narrativa web +{b}/-{s}")
            raw["ddg"] = {"bull_hits": b, "bear_hits": s, "n": len(results)}
            if results:
                conf = max(conf, 0.4)
        except Exception as exc:
            logger.warning("crypto.social.ddg_failed", error=str(exc))

        return self._report(
            ticker,
            score,
            min(0.8, conf),
            "Noticias/redes crypto: " + ("; ".join(bits) or "sin fuentes"),
            opportunities=[
                Finding(
                    category=EvidenceCategory.OPINION,
                    statement=bits[0] if bits else "Percepción social neutra",
                    confidence=conf,
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.INTRADAY,
                )
            ]
            if score > 5
            else [],
            risks=[
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=bits[0] if bits else "Narrativa negativa",
                    confidence=conf,
                    impact=ImpactLevel.MEDIUM,
                    horizon=TimeHorizon.INTRADAY,
                )
            ]
            if score < -5
            else [],
            raw=raw,
        )


class CryptoSentimentAgent(_HeuristicAgent):
    name = "crypto_sentiment_agent"
    label_es = "Crypto · Flujo/volumen"

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
                bits.append("volumen alto + alza (participación)")
            elif rvol > 1.5 and c1 < -3:
                score -= 10
                bits.append("volumen alto en baja")
            else:
                bits.append(f"RVOL {rvol:.1f}×")
        return self._report(
            ticker,
            score,
            0.45 if bits else 0.3,
            "Flujo crypto: " + ("; ".join(bits) or "neutro"),
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
        # Soft caution only — simulation should not be blocked by vol alone
        score = -min(18, vol / 6)
        return self._report(
            ticker,
            score,
            0.5,
            f"Riesgo crypto: vol ~{vol:.0f}% ann. — stops amplios en simulación",
            risks=[
                Finding(
                    category=EvidenceCategory.RISK,
                    statement=f"Volatilidad elevada (~{vol:.0f}% ann.)",
                    confidence=0.55,
                    impact=ImpactLevel.MEDIUM,
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
    "crypto_chart_technical_agent": CryptoChartTechnicalAgent,
    "crypto_news_social_agent": CryptoNewsSocialAgent,
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
