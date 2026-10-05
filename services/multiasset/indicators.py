"""Shared OHLC indicators for multi-asset paper specialists + backtests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    for key in (name, name.capitalize(), name.upper(), name.lower()):
        if key in df.columns:
            return df[key].astype(float)
    raise KeyError(name)


def true_range(df: pd.DataFrame) -> pd.Series:
    high = _col(df, "High")
    low = _col(df, "Low")
    close = _col(df, "Close")
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr


def rma(series: pd.Series, n: int) -> pd.Series:
    """Wilder RMA: seed with SMA of the first n, then (prev*(n-1)+x)/n."""
    s = series.astype(float)
    n = int(n)
    out = pd.Series(index=s.index, dtype=float)
    if len(s) < n:
        return out
    seed = float(s.iloc[:n].mean())
    out.iloc[n - 1] = seed
    prev = seed
    for i in range(n, len(s)):
        x = float(s.iloc[i])
        if not np.isfinite(x):
            out.iloc[i] = prev
            continue
        prev = (prev * (n - 1) + x) / n
        out.iloc[i] = prev
    return out


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR (RMA of true range). Used by Strategy A combo #9."""
    return rma(true_range(df), n)


def atr_sma(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).rolling(n, min_periods=n).mean()


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.astype(float).rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.astype(float).ewm(span=n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    s = series.astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out


def donchian_high(series: pd.Series, n: int) -> pd.Series:
    return series.astype(float).rolling(n, min_periods=n).max()


def donchian_low(series: pd.Series, n: int) -> pd.Series:
    return series.astype(float).rolling(n, min_periods=n).min()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high = _col(df, "High")
    low = _col(df, "Low")
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_n = tr.rolling(n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n).mean() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n).mean() / atr_n
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.rolling(n).mean()


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    close = _col(df, "Close")
    a = atr(df, n)
    return (a / close.replace(0, np.nan)) * 100.0
