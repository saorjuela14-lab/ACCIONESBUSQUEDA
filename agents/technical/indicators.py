"""Technical analysis utilities."""

from typing import Any

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    ta = None


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal, macd - signal


def calc_bollinger(close: pd.Series, period: int = 20, std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    sma = close.rolling(window=period).mean()
    dev = close.rolling(window=period).std()
    return sma + dev * std, sma, sma - dev * std


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["RSI"] = calc_rsi(out["Close"])
    out["MACD"], out["MACD_Signal"], out["MACD_Hist"] = calc_macd(out["Close"])
    out["BB_Upper"], out["BB_Middle"], out["BB_Lower"] = calc_bollinger(out["Close"])
    out["ATR"] = calc_atr(out["High"], out["Low"], out["Close"])
    out["SMA20"] = out["Close"].rolling(20).mean()
    out["SMA50"] = out["Close"].rolling(50).mean()
    out["SMA200"] = out["Close"].rolling(200).mean()
    out["EMA20"] = out["Close"].ewm(span=20, adjust=False).mean()
    out["VWAP"] = (out["Close"] * out["Volume"]).cumsum() / out["Volume"].cumsum()

    if ta is not None:
        out["ADX"] = ta.adx(out["High"], out["Low"], out["Close"])["ADX_14"]
        out["MFI"] = ta.mfi(out["High"], out["Low"], out["Close"], out["Volume"])
        st = ta.supertrend(out["High"], out["Low"], out["Close"])
        if st is not None and not st.empty:
            out["Supertrend"] = st.iloc[:, 0]

    return out


def detect_support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict[str, float]:
    window = df.tail(lookback)
    return {
        "support": float(window["Low"].quantile(0.05)),
        "resistance": float(window["High"].quantile(0.95)),
    }


def build_trade_levels(price: float, support: float, resistance: float, atr: float) -> dict[str, Any]:
    risk = price - (support - atr * 1.5)
    reward = (price + atr * 2) - price
    return {
        "entry_zone": [support, support + atr * 0.5],
        "stop_loss": support - atr * 1.5,
        "take_profit_1": price + atr * 2,
        "take_profit_2": price + atr * 3,
        "take_profit_3": resistance,
        "risk_reward_ratio": round(reward / risk, 2) if risk > 0 else None,
    }
