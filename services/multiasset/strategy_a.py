"""Crypto Strategy A — multi-horizon Donchian on 4h bars, buys only.

Fixed BTC/ETH parameters for every symbol. No per-name optimization at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from services.multiasset.indicators import atr, donchian_high, sma
from utils.logging import get_logger

logger = get_logger(__name__)

# Fixed params (BTC/ETH book) — do not retune per symbol.
DONCHIAN_PERIODS = (30, 60, 120, 240, 480)
FAST_DONCHIAN = 30
SLOW_DONCHIAN = 480
ATR_PERIOD = 14
ATR_STOP_MULT = 2.5
BTC_SMA_DAILY = 200


@dataclass(frozen=True)
class StrategyASignal:
    side: str  # buy | hold
    stop_px: float | None
    atr_abs: float
    reason: str
    extras: dict[str, Any] = field(default_factory=dict)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    for key in (name, name.capitalize(), name.upper(), name.lower()):
        if key in df.columns:
            return df[key].astype(float)
    raise KeyError(name)


def btc_above_daily_sma200(btc_daily: pd.DataFrame | None) -> tuple[bool, str]:
    if btc_daily is None or btc_daily.empty:
        return False, "btc_daily_missing"
    close = _col(btc_daily, "Close")
    if len(close) < BTC_SMA_DAILY:
        return False, "btc_sma200_warmup"
    ma = sma(close, BTC_SMA_DAILY)
    last = float(close.iloc[-1])
    last_ma = float(ma.iloc[-1]) if pd.notna(ma.iloc[-1]) else None
    if last_ma is None:
        return False, "btc_sma200_nan"
    if last > last_ma:
        return True, f"btc_above_sma200 {last:.2f}>{last_ma:.2f}"
    return False, f"btc_below_sma200 {last:.2f}<={last_ma:.2f}"


def round_crypto_px(px: float) -> float:
    return round(float(px), 8)


def stop_is_tradable(entry: float, stop: float) -> bool:
    """Reject rounded stop at 0 or at/above entry."""
    if entry <= 0:
        return False
    s = round_crypto_px(stop)
    if s <= 0:
        return False
    if s >= round_crypto_px(entry):
        return False
    return True


def strategy_a_signal(
    df_4h: pd.DataFrame,
    *,
    btc_daily: pd.DataFrame | None = None,
    btc_filter_ok: bool | None = None,
) -> StrategyASignal:
    """Buy-only Donchian 30 trigger in a 480-bar 4h trend. Same params for all names."""
    if btc_filter_ok is None:
        btc_filter_ok, btc_why = btc_above_daily_sma200(btc_daily)
    else:
        btc_why = "btc_filter_injected"
    if not btc_filter_ok:
        return StrategyASignal("hold", None, 0.0, btc_why, {"btc_filter": False})

    if df_4h is None or df_4h.empty or len(df_4h) < SLOW_DONCHIAN + 2:
        return StrategyASignal("hold", None, 0.0, "warmup_4h", {"need": SLOW_DONCHIAN + 2})

    high = _col(df_4h, "High")
    close = _col(df_4h, "Close")
    i = len(df_4h) - 1
    last = float(close.iloc[i])
    a = atr(df_4h, ATR_PERIOD)
    atr_abs = float(a.iloc[i]) if pd.notna(a.iloc[i]) else 0.0
    stop = round_crypto_px(last - ATR_STOP_MULT * atr_abs) if atr_abs > 0 else 0.0

    broken: list[int] = []
    for n in DONCHIAN_PERIODS:
        dc = donchian_high(high.shift(1), n)
        v = dc.iloc[i]
        if pd.notna(v) and last >= float(v):
            broken.append(n)

    slow = donchian_high(high.shift(1), SLOW_DONCHIAN)
    slow_v = slow.iloc[i]
    in_slow_trend = pd.notna(slow_v) and last >= float(slow_v) * 0.97
    # Mid-channel proxy: last above 480-bar SMA (fixed).
    slow_sma = sma(close, SLOW_DONCHIAN)
    sma_v = slow_sma.iloc[i]
    above_slow_sma = pd.notna(sma_v) and last > float(sma_v)

    extras = {
        "broken": broken,
        "atr": atr_abs,
        "stop_mult": ATR_STOP_MULT,
        "btc_filter": True,
        "above_slow_sma": above_slow_sma,
        "in_slow_trend": in_slow_trend,
    }
    if FAST_DONCHIAN not in broken:
        return StrategyASignal("hold", stop if stop > 0 else None, atr_abs, "no_donchian30_breakout", extras)
    if not (in_slow_trend or above_slow_sma):
        return StrategyASignal("hold", stop if stop > 0 else None, atr_abs, "slow_480_filter", extras)
    if not stop_is_tradable(last, stop):
        return StrategyASignal("hold", stop, atr_abs, "invalid_stop_round", extras)

    return StrategyASignal(
        "buy",
        stop,
        atr_abs,
        f"A donchian30 breakout horizons={broken}",
        extras,
    )


def to_yf_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace(" ", "")
    if "/" in s:
        return s.replace("/", "-")
    if s.endswith("USD") and len(s) > 3:
        return s[:-3] + "-USD"
    return s


def load_ohlc(symbol: str, *, interval: str, period: str = "2y") -> pd.DataFrame:
    """Best-effort yfinance OHLC. Tests should inject frames instead."""
    try:
        import yfinance as yf

        ysym = to_yf_symbol(symbol)
        df = yf.Ticker(ysym).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        if interval in {"60m", "1h", "1H"} and "Close" in df.columns:
            ohlc = df[["Open", "High", "Low", "Close"]].copy()
            if "Volume" in df.columns:
                ohlc["Volume"] = df["Volume"]
            return ohlc.resample("4h").agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    **({"Volume": "sum"} if "Volume" in ohlc.columns else {}),
                }
            ).dropna(how="all")
        return df
    except Exception as exc:
        logger.warning("strategy_a.ohlc_failed", symbol=symbol, interval=interval, error=str(exc))
        return pd.DataFrame()


async def signal_for_symbol(
    symbol: str,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
    btc_daily: pd.DataFrame | None = None,
) -> StrategyASignal:
    """frames may provide '4h' and 'btc_daily' to avoid network in tests."""
    import asyncio

    frames = frames or {}
    df_4h = frames.get("4h")
    btc = frames.get("btc_daily", btc_daily)
    if df_4h is None:
        df_4h = await asyncio.to_thread(load_ohlc, symbol, interval="60m", period="2y")
    if btc is None:
        btc = await asyncio.to_thread(load_ohlc, "BTC/USD", interval="1d", period="2y")
    return strategy_a_signal(df_4h, btc_daily=btc)
