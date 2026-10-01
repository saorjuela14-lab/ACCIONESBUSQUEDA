"""Legacy vs new trade signals (long-only, next-bar entry). No look-ahead."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from services.multiasset.indicators import (
    adx,
    atr,
    atr_pct,
    donchian_high,
    ema,
    rsi,
    sma,
)

Setup = Literal["buy", "hold", "sell"]


@dataclass(frozen=True)
class TradeSignal:
    side: Setup
    stop_pct: float  # distance from entry as fraction (e.g. 0.04 = 4%)
    stop_r: float = 1.0  # always 1R at entry
    trail_atr_mult: float = 0.0
    atr_abs: float = 0.0
    reason: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


# Stops by class for the NEW book (ATR multiples). Legacy uses fixed %.
NEW_STOP_ATR = {"gold": 2.5, "forex": 2.0, "crypto": 2.5}
NEW_TRAIL_ATR = {"gold": 3.0, "forex": 2.5, "crypto": 3.0}
LEGACY_STOP_PCT = {"gold": 0.04, "forex": 0.035, "crypto": 0.08}
LEGACY_TARGET_PCT = {"gold": 0.08, "forex": 0.07, "crypto": 0.16}


def _last(series: pd.Series, i: int) -> float | None:
    if i < 0 or i >= len(series):
        return None
    v = series.iloc[i]
    if pd.isna(v):
        return None
    return float(v)


def _stop_from_atr(close: float, atr_abs: float, mult: float, floor: float = 0.008) -> float:
    if close <= 0 or atr_abs <= 0:
        return floor
    return max(floor, (mult * atr_abs) / close)


def legacy_gold_signal(df: pd.DataFrame, i: int) -> TradeSignal:
    close = df["Close"].astype(float)
    if i < 15:
        return TradeSignal("hold", LEGACY_STOP_PCT["gold"], reason="warmup")
    chg10 = float(close.iloc[i] / close.iloc[i - 10] - 1.0)
    r = _last(rsi(close, 14), i)
    stop = LEGACY_STOP_PCT["gold"]
    if r is not None and r < 35:
        return TradeSignal("buy", stop, reason=f"legacy RSI oversold {r:.0f}", extras={"rsi": r, "chg10": chg10})
    if chg10 > 0.02 and (r is None or r < 70):
        return TradeSignal("buy", stop, reason=f"legacy 10d mom {chg10:.1%}", extras={"rsi": r, "chg10": chg10})
    if chg10 < -0.03 or (r is not None and r > 75):
        return TradeSignal("sell", stop, reason="legacy fade", extras={"rsi": r, "chg10": chg10})
    return TradeSignal("hold", stop, reason="legacy flat", extras={"rsi": r, "chg10": chg10})


def legacy_forex_signal(df: pd.DataFrame, i: int) -> TradeSignal:
    close = df["Close"].astype(float)
    if i < 10:
        return TradeSignal("hold", LEGACY_STOP_PCT["forex"], reason="warmup")
    chg5 = float(close.iloc[i] / close.iloc[i - 5] - 1.0)
    r = _last(rsi(close, 14), i)
    stop = LEGACY_STOP_PCT["forex"]
    if chg5 > 0.012 and (r is None or r < 70):
        return TradeSignal("buy", stop, reason=f"legacy 5d mom {chg5:.1%}", extras={"rsi": r, "chg5": chg5})
    if chg5 < -0.02 or (r is not None and r > 75):
        return TradeSignal("sell", stop, reason="legacy fx fade")
    return TradeSignal("hold", stop, reason="legacy fx flat")


def legacy_crypto_signal(df: pd.DataFrame, i: int) -> TradeSignal:
    close = df["Close"].astype(float)
    stop = LEGACY_STOP_PCT["crypto"]
    if i < 55:
        return TradeSignal("hold", stop, reason="warmup")
    e50 = _last(ema(close, 50), i)
    r = _last(rsi(close, 14), i)
    last = float(close.iloc[i])
    if e50 is None:
        return TradeSignal("hold", stop, reason="no ema")
    if last > e50 and r is not None and 40 <= r <= 68:
        return TradeSignal("buy", stop, reason=f"legacy ema50+RSI {r:.0f}", extras={"rsi": r, "ema50": e50})
    if last < e50 and r is not None and r > 70:
        return TradeSignal("sell", stop, reason="legacy crypto overbought downtrend")
    return TradeSignal("hold", stop, reason="legacy crypto flat")


def new_gold_signal(df: pd.DataFrame, i: int, *, dxy_10d: float | None = None) -> TradeSignal:
    """Donchian 20 breakout + SMA50 + DXY filter. ATR stop 2.5×, trail 3×."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    stop_mult = NEW_STOP_ATR["gold"]
    trail = NEW_TRAIL_ATR["gold"]
    if i < 55:
        return TradeSignal("hold", 0.04, trail_atr_mult=trail, reason="warmup")
    a = _last(atr(df, 14), i) or 0.0
    last = float(close.iloc[i])
    stop_pct = _stop_from_atr(last, a, stop_mult, floor=0.012)
    sma50 = _last(sma(close, 50), i)
    dc = _last(donchian_high(high.shift(1), 20), i)  # prior 20-day high — no look-ahead
    if sma50 is None or dc is None:
        return TradeSignal("hold", stop_pct, 1.0, trail, a, "incomplete")
    if dxy_10d is not None and dxy_10d > 0.015:
        return TradeSignal("hold", stop_pct, 1.0, trail, a, f"DXY filter {dxy_10d:.1%}", extras={"dxy_10d": dxy_10d})
    if last > sma50 and last >= dc:
        return TradeSignal(
            "buy",
            stop_pct,
            1.0,
            trail,
            a,
            f"gold breakout DC20 + SMA50 ATR {stop_pct:.1%}",
            extras={"sma50": sma50, "donchian": dc, "dxy_10d": dxy_10d},
        )
    if last < sma50 * 0.98:
        return TradeSignal("sell", stop_pct, 1.0, trail, a, "below SMA50")
    return TradeSignal("hold", stop_pct, 1.0, trail, a, "no breakout")


def new_forex_signal(df: pd.DataFrame, i: int) -> TradeSignal:
    """12–1 month-ish TSMOM with ADX regime. Skip last ~20d to avoid reversal noise."""
    close = df["Close"].astype(float)
    stop_mult = NEW_STOP_ATR["forex"]
    trail = NEW_TRAIL_ATR["forex"]
    if i < 80:
        return TradeSignal("hold", 0.035, trail_atr_mult=trail, reason="warmup")
    a = _last(atr(df, 14), i) or 0.0
    last = float(close.iloc[i])
    stop_pct = _stop_from_atr(last, a, stop_mult, floor=0.01)
    adx_v = _last(adx(df, 14), i)
    mom = float(close.iloc[i - 20] / close.iloc[i - 80] - 1.0)  # skip most recent month
    if adx_v is not None and adx_v < 16:
        r = _last(rsi(close, 14), i)
        if r is not None and r < 30:
            return TradeSignal("buy", stop_pct, 1.0, trail, a, f"fx range RSI {r:.0f}", extras={"adx": adx_v, "mom": mom})
        return TradeSignal("hold", stop_pct, 1.0, trail, a, f"low ADX {adx_v:.0f} skip momentum")
    if mom > 0.02:
        return TradeSignal("buy", stop_pct, 1.0, trail, a, f"fx TSMOM {mom:.1%} ADX {adx_v}", extras={"adx": adx_v, "mom": mom})
    if mom < -0.03:
        return TradeSignal("sell", stop_pct, 1.0, trail, a, f"fx TSMOM down {mom:.1%}")
    return TradeSignal("hold", stop_pct, 1.0, trail, a, "fx mom flat")


def new_crypto_signal(df: pd.DataFrame, i: int) -> TradeSignal:
    """Donchian 10 breakout in EMA50 uptrend, skip extreme ATR%. Trail 3× ATR."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    stop_mult = NEW_STOP_ATR["crypto"]
    trail = NEW_TRAIL_ATR["crypto"]
    if i < 55:
        return TradeSignal("hold", 0.08, trail_atr_mult=trail, reason="warmup")
    a = _last(atr(df, 14), i) or 0.0
    last = float(close.iloc[i])
    stop_pct = _stop_from_atr(last, a, stop_mult, floor=0.03)
    ap = _last(atr_pct(df, 14), i)
    if ap is not None and ap > 12:
        return TradeSignal("hold", stop_pct, 1.0, trail, a, f"vol too wild ATR% {ap:.1f}")
    e50 = _last(ema(close, 50), i)
    dc = _last(donchian_high(high.shift(1), 10), i)
    r = _last(rsi(close, 14), i)
    if e50 is None or dc is None:
        return TradeSignal("hold", stop_pct, 1.0, trail, a, "incomplete")
    if last > e50 and last >= dc and (r is None or r < 78):
        return TradeSignal(
            "buy",
            stop_pct,
            1.0,
            trail,
            a,
            f"crypto DC10 breakout ATR stop {stop_pct:.1%}",
            extras={"ema50": e50, "rsi": r, "atr_pct": ap},
        )
    if last < e50 * 0.97 or (r is not None and r > 80):
        return TradeSignal("sell", stop_pct, 1.0, trail, a, "crypto trend break / late")
    return TradeSignal("hold", stop_pct, 1.0, trail, a, "crypto no breakout")


SIGNAL_FNS = {
    ("gold", "legacy"): legacy_gold_signal,
    ("gold", "new"): new_gold_signal,
    ("forex", "legacy"): legacy_forex_signal,
    ("forex", "new"): new_forex_signal,
    ("crypto", "legacy"): legacy_crypto_signal,
    ("crypto", "new"): new_crypto_signal,
}
