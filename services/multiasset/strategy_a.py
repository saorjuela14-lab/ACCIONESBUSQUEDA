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
    """Compare close vs PREVIOUS stop, then ratchet. Fill at next open."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    completed = last_completed_frame(df_4h, now=clock)
    candle_open = None
    if completed is not None and not completed.empty and isinstance(completed.index, pd.DatetimeIndex):
        candle_open = pd.Timestamp(completed.index[-1])
        if candle_open.tzinfo is None:
            candle_open = candle_open.tz_localize("UTC")
        else:
            candle_open = candle_open.tz_convert("UTC")
    out: dict[str, Any] = {
        "hit": False,
        "execute_now": False,
        "pending_exit": bool(pending_exit),
        "stop_px": round_crypto_px(float(stop_px or 0)) if stop_px else None,
        "highest_close": float(highest_close or 0),
        "candle_close": None,
        "candle_ts": None,
        "candle_open": candle_open.isoformat() if candle_open is not None else None,
        "stop_evaluated_at": candle_open.isoformat() if candle_open is not None else None,
        "clock_evaluated_at": clock.isoformat(),
        "atr": 0.0,
        "broker_stop": "none",
        "reason": "no_completed_bar",
        "data_ok": True,
    }
    if completed is None or completed.empty:
        out["data_ok"] = False
        out["reason"] = "data_insufficient"
        return out
    close = float(_col(completed, "Close").iloc[-1])
    a = atr(completed, ATR_PERIOD)
    atr_abs = float(a.iloc[-1]) if len(a) and pd.notna(a.iloc[-1]) else 0.0
    prev = float(stop_px or 0)
    hit = bool(prev > 0 and close <= prev)
    new_high = max(float(highest_close or 0), close)
    new_stop = chandelier_stop_px(new_high, atr_abs, prev_stop=stop_px)
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
    """Release when S goes through 0 then up, OR S rises above S-at-stop (engine)."""
    b = dict(block or {})
    if not b.get("blocked"):
        return {"blocked": False, "seen_zero": False, "s_at_stop": b.get("s_at_stop")}
    s_f = float(s)
    s_at = b.get("s_at_stop")
    try:
        s_at_f = float(s_at) if s_at is not None else None
    except (TypeError, ValueError):
        s_at_f = None
    seen_zero = bool(b.get("seen_zero")) or s_f <= 1e-12
    if s_f <= 1e-12:
        return {"blocked": True, "seen_zero": True, "s_at_stop": s_at_f}
    if seen_zero and s_f > 1e-12:
        return {"blocked": False, "seen_zero": False, "s_at_stop": s_at_f}
    if s_at_f is not None and s_f > s_at_f + 1e-12:
        return {"blocked": False, "seen_zero": False, "s_at_stop": s_at_f}
    return {"blocked": True, "seen_zero": seen_zero, "s_at_stop": s_at_f}


def arm_post_stop_block(s: float) -> dict[str, Any]:
    s_f = float(s)
    return {"blocked": True, "seen_zero": s_f <= 1e-12, "s_at_stop": s_f}


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
        "data_ok": True,
    }
    if btc_4h is not None and (btc_completed is None or btc_completed.empty):
        extras["data_ok"] = False
        return StrategyASignal("hold", None, 0.0, "data_insufficient", extras, S=float("nan"))
    if not btc_filter_ok:
        # Missing BTC frame is no-op; a real below-SMA is S=0 (regime off).
        if btc_why in {"btc_4h_missing", "btc_sma200_warmup", "btc_sma200_nan"}:
            extras["data_ok"] = False
            return StrategyASignal("hold", None, 0.0, "data_insufficient", extras, S=float("nan"))
        return StrategyASignal("hold", None, 0.0, btc_why, extras, S=0.0)

    need = max(DONCHIAN_L) + 2
    if completed is None or completed.empty:
        extras["data_ok"] = False
        extras["need"] = need
        return StrategyASignal("hold", None, 0.0, "data_insufficient", extras, S=float("nan"))
    if len(completed) < need:
        extras["need"] = need
        extras["data_ok"] = False
        return StrategyASignal("hold", None, 0.0, "data_insufficient", extras, S=float("nan"))

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
            "data_ok": True,
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
    """Deprecated sync path. Strategy A runtime uses Alpaca 1h → clean → 4h."""
    del interval, period
    logger.warning("strategy_a.load_ohlc_deprecated", symbol=symbol)
    return pd.DataFrame()


def rebuild_highest_close(
    df_4h: pd.DataFrame | None,
    *,
    entry_ts: datetime | str | None,
    persisted: float | None,
) -> tuple[float | None, str]:
    """Max close since entry from history. Never reset to entry price alone."""
    completed = last_completed_frame(df_4h) if df_4h is not None else pd.DataFrame()
    if completed is None or completed.empty:
        if persisted and float(persisted) > 0:
            return float(persisted), "db"
        return None, "missing"
    close = _col(completed, "Close")
    if entry_ts:
        try:
            ts = pd.Timestamp(entry_ts)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            if isinstance(completed.index, pd.DatetimeIndex):
                idx = completed.index
                if idx.tz is None:
                    ts = ts.tz_localize(None) if ts.tzinfo else ts
                close = close.loc[idx >= ts]
        except Exception:
            pass
    if close.empty:
        if persisted and float(persisted) > 0:
            return float(persisted), "db"
        return None, "missing"
    hist = float(close.max())
    if persisted and float(persisted) > hist:
        return float(persisted), "db"
    source = "db" if persisted and float(persisted) > 0 else "rebuilt"
    return hist, source


def catch_up_exits(
    df_4h: pd.DataFrame | None,
    *,
    last_evaluated_open: datetime | str | None,
    highest_close: float | None,
    stop_px: float | None,
    now: datetime | None = None,
    block: dict[str, Any] | None = None,
    s_series: pd.Series | None = None,
) -> dict[str, Any]:
    """Walk every closed 4h bar since last_evaluated_open, in order.

    Engine order per bar: compare close vs the *previous* chandelier stop,
    then raise max_close / Wilder ATR / stop. A recovered bar that closed
    under the stop → sell_now and late:true. Entries/rebalances use only
    signal_bar (the last closed candle); recovered bars never emit a signal.
    """
    from services.multiasset.engine_bars import drop_forming_bar

    clock = now or datetime.now(timezone.utc)
    completed = drop_forming_bar(last_completed_frame(df_4h, now=clock), now=clock)
    evals: list[dict[str, Any]] = []
    high = float(highest_close or 0)
    prev_stop = float(stop_px or 0) or None
    hit_bar: dict[str, Any] | None = None
    block_state = dict(block or {})
    empty = {
        "data_ok": False,
        "missed_candles": 0,
        "evals": [],
        "highest_close": high,
        "stop_px": prev_stop,
        "hit": False,
        "hit_bar": None,
        "last_evaluated_candle": None,
        "signal_bar": None,
        "block": block_state,
    }
    if completed is None or completed.empty:
        return empty
    if not isinstance(completed.index, pd.DatetimeIndex):
        bars = completed.iloc[-1:]
    elif last_evaluated_open:
        ts = pd.Timestamp(last_evaluated_open)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        idx = completed.index
        if idx.tz is None:
            ts = ts.tz_localize(None)
        bars = completed.loc[idx > ts]
    else:
        bars = completed.iloc[-1:]
    if bars is None or getattr(bars, "empty", True):
        empty["data_ok"] = True
        empty["last_evaluated_candle"] = (
            pd.Timestamp(last_evaluated_open).isoformat() if last_evaluated_open else None
        )
        return empty

    n_bars = len(bars)
    clock_ts = pd.Timestamp(clock)
    if clock_ts.tzinfo is None:
        clock_ts = clock_ts.tz_localize("UTC")
    else:
        clock_ts = clock_ts.tz_convert("UTC")

    for i in range(n_bars):
        row = bars.iloc[i : i + 1]
        close = float(_col(row, "Close").iloc[-1])
        hist = completed.loc[: row.index[-1]]
        a = atr(hist, ATR_PERIOD)
        atr_abs = float(a.iloc[-1]) if len(a) and pd.notna(a.iloc[-1]) else 0.0
        # 1) compare vs previous stop  2) then raise max_close / chandelier
        hit = bool(prev_stop and prev_stop > 0 and close <= float(prev_stop))
        high = max(high, close)
        new_stop = chandelier_stop_px(high, atr_abs, prev_stop=prev_stop)
        open_ts = row.index[-1]
        ts_open = pd.Timestamp(open_ts)
        if ts_open.tzinfo is None:
            ts_open = ts_open.tz_localize("UTC")
        else:
            ts_open = ts_open.tz_convert("UTC")
        close_at = ts_open + pd.Timedelta(hours=BAR_HOURS)
        is_last = i == n_bars - 1
        recovered = (not is_last) or bool(clock_ts > close_at + pd.Timedelta(minutes=15))
        if s_series is not None:
            try:
                s_now = float(s_series.loc[row.index[-1]])
            except Exception:
                s_now = float("nan")
            if np.isfinite(s_now):
                block_state = update_post_stop_block(block_state, s_now)
        rec = {
            "candle_open": ts_open.isoformat(),
            "candle_close": close,
            "stop_px": new_stop if new_stop > 0 else prev_stop,
            "highest_close": high,
            "hit": hit,
            "atr": atr_abs,
            "late": bool(recovered),
            "evaluated_at": clock_ts.isoformat(),
            "sell_now": False,
        }
        prev_stop = new_stop if new_stop and new_stop > 0 else prev_stop
        evals.append(rec)
        if hit:
            rec["sell_now"] = True
            # Missed candle under stop → market sell + late:true.
            if recovered:
                rec["late"] = True
            hit_bar = rec
            break

    last_open = evals[-1]["candle_open"] if evals else (
        pd.Timestamp(last_evaluated_open).isoformat() if last_evaluated_open else None
    )
    missed = 0
    if last_evaluated_open:
        missed = sum(1 for e in evals if e.get("late"))
        if missed == 0 and len(evals) > 1:
            missed = len(evals) - 1
    # Entries / rebalances: last closed bar only, and never after a recovered stop hit.
    signal_bar = None
    if not hit_bar and evals:
        signal_bar = evals[-1]
    return {
        "data_ok": True,
        "missed_candles": int(missed),
        "evals": evals,
        "highest_close": (hit_bar or evals[-1])["highest_close"] if evals else high,
        "stop_px": (hit_bar or evals[-1])["stop_px"] if evals else prev_stop,
        "hit": bool(hit_bar),
        "hit_bar": hit_bar,
        "last_evaluated_candle": last_open,
        "signal_bar": signal_bar,
        "block": block_state,
    }


async def signal_for_symbol(
    symbol: str,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
    btc_4h: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> StrategyASignal:
    """frames may provide '4h' and 'btc_4h' to avoid network in tests."""
    from services.multiasset.engine_bars import load_strategy_a_4h

    frames = frames or {}
    df_4h = frames.get("4h")
    btc = frames.get("btc_4h", btc_4h)
    if df_4h is None:
        df_4h = await load_strategy_a_4h(symbol, now=now)
    if btc is None:
        btc = await load_strategy_a_4h("BTC/USD", now=now)
    return strategy_a_signal(df_4h, btc_4h=btc, now=now)
