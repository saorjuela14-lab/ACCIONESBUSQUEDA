"""In-sample historical edge for common TA setups on this ticker's OHLC."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _forward_hit_rate(signal: pd.Series, close: pd.Series, horizon: int, direction: str) -> dict[str, Any] | None:
    """Share of signals where forward return moves in expected direction."""
    if signal.sum() < 8:
        return None
    rets = close.pct_change(horizon).shift(-horizon)
    paired = pd.DataFrame({"sig": signal.astype(bool), "fwd": rets}).dropna()
    hits = paired[paired["sig"]]
    if len(hits) < 8:
        return None
    if direction == "long":
        win = (hits["fwd"] > 0).mean()
        avg = hits["fwd"].mean()
    else:
        win = (hits["fwd"] < 0).mean()
        avg = (-hits["fwd"]).mean()
    return {
        "samples": int(len(hits)),
        "hit_rate": round(float(win) * 100, 1),
        "avg_forward_return_pct": round(float(avg) * 100, 2),
        "horizon_bars": horizon,
    }


def evaluate_historical_setups(df: pd.DataFrame) -> dict[str, Any]:
    """
    Measure simple historical edges on this series (educational, in-sample).

    Setups inspired by common discretionary playbooks:
    - RSI oversold bounce (long)
    - RSI overbought fade (short)
    - MACD bullish cross continuation (long)
    - Trend filter: close > SMA200 continuation (long)
    """
    if df.empty or len(df) < 80:
        return {"available": False, "setups": {}, "note": "Histórico insuficiente (<80 barras)."}

    close = df["Close"]
    rsi = df["RSI"] if "RSI" in df.columns else None
    macd = df["MACD"] if "MACD" in df.columns else None
    macd_sig = df["MACD_Signal"] if "MACD_Signal" in df.columns else None
    sma200 = df["SMA200"] if "SMA200" in df.columns else None

    setups: dict[str, Any] = {}

    if rsi is not None:
        oversold = (rsi < 30) & (rsi.shift(1) >= 30)
        overbought = (rsi > 70) & (rsi.shift(1) <= 70)
        edge = _forward_hit_rate(oversold.fillna(False), close, 5, "long")
        if edge:
            setups["rsi_oversold_bounce_5"] = {
                **edge,
                "label_es": "Rebote tras RSI sobreventa",
                "bias": "long",
            }
        edge = _forward_hit_rate(overbought.fillna(False), close, 5, "short")
        if edge:
            setups["rsi_overbought_fade_5"] = {
                **edge,
                "label_es": "Retroceso tras RSI sobrecompra",
                "bias": "short",
            }

    if macd is not None and macd_sig is not None:
        cross_up = (macd > macd_sig) & (macd.shift(1) <= macd_sig.shift(1))
        edge = _forward_hit_rate(cross_up.fillna(False), close, 5, "long")
        if edge:
            setups["macd_bull_cross_5"] = {
                **edge,
                "label_es": "Cruce alcista MACD",
                "bias": "long",
            }

    if sma200 is not None:
        trend_long = (close > sma200) & (close.shift(1) <= sma200.shift(1))
        edge = _forward_hit_rate(trend_long.fillna(False), close, 10, "long")
        if edge:
            setups["sma200_reclaim_10"] = {
                **edge,
                "label_es": "Recuperación sobre SMA200",
                "bias": "long",
            }

    # Best edge for narrative
    best = None
    for key, val in setups.items():
        if best is None or val["hit_rate"] > best["hit_rate"]:
            best = {**val, "key": key}

    note = (
        "Estadísticas in-sample sobre este ticker (no predicción futura). "
        "Úsalas como contexto de edge relativo, no como garantía."
    )
    return {
        "available": bool(setups),
        "setups": setups,
        "best": best,
        "note": note,
    }
