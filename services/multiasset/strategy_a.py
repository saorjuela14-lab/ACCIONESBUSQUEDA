"""Crypto Strategy A — backtested combination #9 (PAPER only).

4h Donchian hysteresis L={120,240,480}; S = mean of the three channels.
BTC regime = close > SMA(1.200) on 4h bars (200d), applied to every coin.
Chandelier stop on closes: max close since entry − 8×ATR14; ratchet up only.
Evaluate at completed 4h close; market exit at the next bar's open (software).
No fixed TP. No broker stop/bracket/OCO/trailing (Alpaca crypto cannot).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from services.multiasset.indicators import atr, sma
from utils.logging import get_logger

logger = get_logger(__name__)

# Combination #9 — do not retune per symbol at runtime.
DONCHIAN_L = (120, 240, 480)
ATR_PERIOD = 14
CHANDELIER_ATR_MULT = 8.0
BTC_SMA_4H_BARS = 1200  # 200 days × 6 bars/day
VOL_LOOKBACK_BARS = 180  # 30d × 6
VOL_TARGET = 0.25
BARS_PER_DAY = 6
BAR_HOURS = 4
REBALANCE_DEV = 0.20
COMBO_ID = 9


@dataclass(frozen=True)
class StrategyASignal:
    side: str  # buy | hold
    stop_px: float | None
    atr_abs: float
    reason: str
    extras: dict[str, Any] = field(default_factory=dict)
    S: float = 0.0
    vol_30d: float | None = None


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    for key in (name, name.capitalize(), name.upper(), name.lower()):
        if key in df.columns:
            return df[key].astype(float)
    raise KeyError(name)


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


def last_completed_frame(df: pd.DataFrame | None, now: datetime | None = None) -> pd.DataFrame:
    """Drop the in-progress 4h bar so signals/stops use a closed candle only."""
    if df is None or df.empty:
        return pd.DataFrame()
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 2:
        return df
    ts = df.index[-1]
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    if ts.tzinfo != clock.tzinfo:
        clock = clock.astimezone(ts.tzinfo)
    if clock < ts + pd.Timedelta(hours=BAR_HOURS):
        return df.iloc[:-1]
    return df


def _bar_ts(df: pd.DataFrame) -> str | None:
    if df is None or df.empty:
        return None
    idx = df.index[-1]
    try:
        return pd.Timestamp(idx).isoformat()
    except Exception:
        return None


def hysteresis_channel(df: pd.DataFrame, L: int) -> np.ndarray:
    """Sticky Donchian: ON if close > max(high of prior L); OFF if close < min(low of prior L/2)."""
    high = _col(df, "High")
    low = _col(df, "Low")
    close = _col(df, "Close")
    prev_max = high.shift(1).rolling(int(L), min_periods=int(L)).max()
    prev_min_half = low.shift(1).rolling(int(L) // 2, min_periods=int(L) // 2).min()
    n = len(df)
    state = np.zeros(n, dtype=float)
    on = 0.0
    for i in range(n):
        mx = prev_max.iloc[i]
        mn = prev_min_half.iloc[i]
        c = float(close.iloc[i])
        if pd.isna(mx):
            state[i] = 0.0
            continue
        if c > float(mx):
            on = 1.0
        elif pd.notna(mn) and c < float(mn):
            on = 0.0
        state[i] = on
    return state


def donchian_S(df: pd.DataFrame, periods: tuple[int, ...] = DONCHIAN_L) -> tuple[float, dict[int, int]]:
    components: dict[int, int] = {}
    vals: list[float] = []
    for L in periods:
        ch = hysteresis_channel(df, int(L))
        bit = int(ch[-1]) if len(ch) else 0
        components[int(L)] = bit
        vals.append(float(bit))
    s = float(sum(vals) / len(vals)) if vals else 0.0
    return s, components


def btc_regime_ok(btc_4h: pd.DataFrame | None) -> tuple[bool, str]:
    """BTC 4h close > SMA of 1.200 4h bars (200 daily sessions). Same flag for every coin."""
    if btc_4h is None or btc_4h.empty:
        return False, "btc_4h_missing"
    close = _col(btc_4h, "Close")
    if len(close) < BTC_SMA_4H_BARS:
        return False, "btc_sma200_warmup"
    ma = sma(close, BTC_SMA_4H_BARS)
    last = float(close.iloc[-1])
    last_ma = float(ma.iloc[-1]) if pd.notna(ma.iloc[-1]) else None
    if last_ma is None:
        return False, "btc_sma200_nan"
    if last > last_ma:
        return True, f"btc_above_sma200_4h {last:.2f}>{last_ma:.2f}"
    return False, f"btc_below_sma200_4h {last:.2f}<={last_ma:.2f}"


def realized_vol_30d(df_4h: pd.DataFrame) -> float | None:
    """Annualized 30d realized vol from 4h log returns (6×365)."""
    if df_4h is None or df_4h.empty:
        return None
    close = _col(df_4h, "Close")
    if len(close) < VOL_LOOKBACK_BARS + 1:
        return None
    log_r = np.log(close.replace(0, np.nan)).diff()
    window = log_r.iloc[-VOL_LOOKBACK_BARS:]
    std = float(window.std(ddof=1)) if window.notna().sum() >= 10 else float("nan")
    if not np.isfinite(std) or std <= 0:
        return None
    return float(std * np.sqrt(BARS_PER_DAY * 365.0))


def vol_scale(vol_30d: float | None) -> float | None:
    if vol_30d is None or not np.isfinite(vol_30d) or vol_30d <= 0:
        return None
    return float(min(1.0, VOL_TARGET / float(vol_30d)))


def chandelier_stop_px(highest_close: float, atr_abs: float, prev_stop: float | None = None) -> float:
    if atr_abs <= 0 or highest_close <= 0:
        return 0.0
    cand = round_crypto_px(float(highest_close) - CHANDELIER_ATR_MULT * float(atr_abs))
    if prev_stop and float(prev_stop) > 0:
        return round_crypto_px(max(float(prev_stop), cand))
    return cand


def evaluate_chandelier_exit(
    df_4h: pd.DataFrame,
    *,
    highest_close: float,
    stop_px: float | None,
    pending_exit: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stop vs completed 4h close; fill at next open. Never uses the in-progress print."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    completed = last_completed_frame(df_4h, now=clock)
    out: dict[str, Any] = {
        "hit": False,
        "execute_now": False,
        "pending_exit": bool(pending_exit),
        "stop_px": round_crypto_px(float(stop_px or 0)) if stop_px else None,
        "highest_close": float(highest_close or 0),
        "candle_close": None,
        "candle_ts": None,
        "stop_evaluated_at": clock.isoformat(),
        "atr": 0.0,
        "broker_stop": "none",
        "reason": "no_completed_bar",
    }
    if completed is None or completed.empty:
        return out
    close = float(_col(completed, "Close").iloc[-1])
    a = atr(completed, ATR_PERIOD)
    atr_abs = float(a.iloc[-1]) if len(a) and pd.notna(a.iloc[-1]) else 0.0
    new_high = max(float(highest_close or 0), close)
    new_stop = chandelier_stop_px(new_high, atr_abs, prev_stop=stop_px)
    hit = bool(atr_abs > 0 and new_stop > 0 and close <= new_stop)
    has_dt = isinstance(df_4h.index, pd.DatetimeIndex) and isinstance(completed.index, pd.DatetimeIndex)
    next_open_in_df = len(df_4h) > len(completed)
    next_open_by_clock = False
    last_ts = completed.index[-1] if has_dt else None
    if last_ts is not None:
        ts = pd.Timestamp(last_ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        next_open_by_clock = pd.Timestamp(clock) >= ts + pd.Timedelta(hours=BAR_HOURS)
    if not has_dt:
        if pending_exit:
            execute_now, pending = True, False
        elif hit:
            execute_now, pending = False, True
        else:
            execute_now, pending = False, False
    else:
        next_open = next_open_in_df or next_open_by_clock
        if pending_exit and next_open:
            execute_now, pending = True, False
        elif hit and next_open:
            execute_now, pending = True, False
        elif hit:
            execute_now, pending = False, True
        else:
            execute_now, pending = False, False

    out.update(
        {
            "hit": hit,
            "execute_now": execute_now,
            "pending_exit": pending,
            "stop_px": new_stop if new_stop > 0 else None,
            "highest_close": new_high,
            "candle_close": close,
            "candle_ts": _bar_ts(completed),
            "atr": atr_abs,
            "reason": "chandelier_hit" if hit else "ok",
        }
    )
    return out


def update_post_stop_block(block: dict[str, Any] | None, s: float) -> dict[str, Any]:
    """After a stop, stay blocked until S goes to 0 and then rises again."""
    b = dict(block or {})
    if not b.get("blocked"):
        return {"blocked": False, "seen_zero": False}
    if float(s) <= 1e-12:
        return {"blocked": True, "seen_zero": True}
    if b.get("seen_zero") and float(s) > 1e-12:
        return {"blocked": False, "seen_zero": False}
    return {"blocked": True, "seen_zero": bool(b.get("seen_zero"))}


def arm_post_stop_block(s: float) -> dict[str, Any]:
    return {"blocked": True, "seen_zero": float(s) <= 1e-12}


def should_rebalance(
    current_notional: float,
    target_notional: float,
    prev_s: float | None,
    s: float,
    *,
    dev: float = REBALANCE_DEV,
) -> bool:
    if prev_s is not None and abs(float(prev_s) - float(s)) > 1e-12:
        return True
    if float(target_notional) <= 0:
        return float(current_notional) > 0
    if float(current_notional) <= 0:
        return float(target_notional) > 0
    return abs(float(current_notional) - float(target_notional)) / float(target_notional) > float(dev)


def vol_weight_notional(*, s: float, vol_30d: float | None, name_cap_notional: float) -> float:
    scale = vol_scale(vol_30d)
    if scale is None or float(s) <= 0:
        return 0.0
    return float(s) * scale * max(0.0, float(name_cap_notional))


def strategy_a_signal(
    df_4h: pd.DataFrame,
    *,
    btc_4h: pd.DataFrame | None = None,
    btc_filter_ok: bool | None = None,
    now: datetime | None = None,
) -> StrategyASignal:
    """Buy-only combo #9. Same params for every name on the JSON universe."""
    completed = last_completed_frame(df_4h, now=now)
    btc_completed = last_completed_frame(btc_4h, now=now) if btc_4h is not None else None
    if btc_filter_ok is None:
        btc_filter_ok, btc_why = btc_regime_ok(btc_completed)
    else:
        btc_why = "btc_filter_injected"
    extras: dict[str, Any] = {
        "combo": COMBO_ID,
        "donchian_L": list(DONCHIAN_L),
        "chandelier_atr_mult": CHANDELIER_ATR_MULT,
        "btc_sma_4h_bars": BTC_SMA_4H_BARS,
        "tp": None,
        "broker_stop": "none",
        "btc_filter": bool(btc_filter_ok),
        "S": 0.0,
        "components": {str(L): 0 for L in DONCHIAN_L},
    }
    if not btc_filter_ok:
        return StrategyASignal("hold", None, 0.0, btc_why, extras, S=0.0)

    need = max(DONCHIAN_L) + 2
    if completed is None or completed.empty or len(completed) < need:
        extras["need"] = need
        return StrategyASignal("hold", None, 0.0, "warmup_4h", extras, S=0.0)

    close = _col(completed, "Close")
    last = float(close.iloc[-1])
    a = atr(completed, ATR_PERIOD)
    atr_abs = float(a.iloc[-1]) if pd.notna(a.iloc[-1]) else 0.0
    s, components = donchian_S(completed)
    vol = realized_vol_30d(completed)
    stop = chandelier_stop_px(last, atr_abs)
    extras.update(
        {
            "S": s,
            "components": {str(k): int(v) for k, v in components.items()},
            "atr": atr_abs,
            "vol_30d": vol,
            "vol_scale": vol_scale(vol),
            "candle_close": last,
            "candle_ts": _bar_ts(completed),
            "btc_why": btc_why,
        }
    )
    if s <= 1e-12:
        return StrategyASignal("hold", stop if stop > 0 else None, atr_abs, "S_zero", extras, S=0.0, vol_30d=vol)
    if not stop_is_tradable(last, stop):
        return StrategyASignal("hold", stop, atr_abs, "invalid_stop_round", extras, S=s, vol_30d=vol)
    return StrategyASignal(
        "buy",
        stop,
        atr_abs,
        f"A combo9 S={s:.3f} L={components}",
        extras,
        S=s,
        vol_30d=vol,
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
    btc_4h: pd.DataFrame | None = None,
) -> StrategyASignal:
    """frames may provide '4h' and 'btc_4h' to avoid network in tests."""
    import asyncio

    frames = frames or {}
    df_4h = frames.get("4h")
    btc = frames.get("btc_4h", btc_4h)
    if df_4h is None:
        df_4h = await asyncio.to_thread(load_ohlc, symbol, interval="60m", period="2y")
    if btc is None:
        btc = await asyncio.to_thread(load_ohlc, "BTC/USD", interval="60m", period="2y")
    return strategy_a_signal(df_4h, btc_4h=btc)
