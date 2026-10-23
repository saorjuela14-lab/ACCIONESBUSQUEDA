"""Research backtest for Monarch Capital micro exits.

Research-only: no live imports, no secrets, no broker writes. Signals use closed
1D bars and entries occur on the next available open.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf

BASE_EQUITY = 21.74
PROP_BRAKE_PCT = 0.95
STOP_PCT = 0.08
TARGET_PCT = 0.16
TRAIL_ARM_PCT = 0.05
TRAILING_PCT = 0.10
MIN_NOTIONAL_WHOLE = 3.0

DEFAULT_UNIVERSE = (
    "PLUG", "BITF", "AMC", "BBAI", "ABEV", "OPEN", "CLOV", "SNAP", "NIO",
    "SENS", "SOUN", "BB", "NOK", "F", "AAL", "SOFI", "SIRI", "T", "VZ",
    "PFE", "INTC", "KEY", "HBAN", "RF", "WBD", "PCG", "KMI", "AGNC", "NLY",
    "FCEL", "RIOT", "MARA", "ACHR", "JOBY", "LUNR", "ASTS", "CIFR", "APLD",
    "GRAB", "CHPT", "SPCE", "DNA", "MVST", "LAZR", "VALE", "ITUB", "PBR",
    "BBD", "GOLD", "NU", "CFG", "JD", "BIDU", "XPEV", "LI", "HOOD", "UPST",
    "PATH", "RIG", "HAL", "HL", "AG", "CDE", "BTBT", "CAN", "HUT", "CLSK",
    "WULF", "IREN", "IONQ", "RXRX", "ARR", "TWO", "ORC", "MPW",
)
LEGACY_OR_DELISTED = ("NKLA", "WISH", "BBIG")


@dataclass(frozen=True)
class Variant:
    name: str
    eod_green_harvest: bool = False
    symmetric_overnight: bool = True
    trend_filter: bool = False
    cooldown_same_symbol_sessions: int = 0
    cooldown_global_after_stop_sessions: int = 0


VARIANTS = (
    Variant(name="actual_eod", eod_green_harvest=True, symmetric_overnight=False),
    Variant(name="simetrica"),
    Variant(name="simetrica_trend", trend_filter=True),
    Variant(name="cooldown_1d", trend_filter=True, cooldown_same_symbol_sessions=1, cooldown_global_after_stop_sessions=1),
    Variant(name="cooldown_5d", trend_filter=True, cooldown_same_symbol_sessions=5, cooldown_global_after_stop_sessions=1),
)


@dataclass
class Trade:
    variant: str
    sample: str
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    qty: int
    entry: float
    exit: float
    notional_usd: float
    equity_before: float
    equity_after: float
    fee_usd: float
    pnl_usd: float
    pnl_pct: float
    r_multiple: float
    gross_r_multiple: float
    stop: float
    target: float
    mfe_r: float
    mae_r: float
    duration_days: int
    exit_reason: str
    stop_gap: bool
    tp_gap: bool
    proposed_brake_after_trade: bool
    session_brake_after_trade: bool
    atr_pct_at_entry: float | None
    tp_atr_multiple: float | None
    signal_score: float
    signal_rsi: float | None
    signal_chg5_pct: float | None
    signal_vol_spike: float | None
    signal_adx: float | None
    signal_extension_sma20_pct: float | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--split-date", default="")
    p.add_argument("--capital", type=float, default=21.76)
    p.add_argument("--base", type=float, default=BASE_EQUITY)
    p.add_argument("--out-dir", default="research/backtest/output")
    p.add_argument("--universe", default="")
    p.add_argument("--include-legacy", action="store_true")
    p.add_argument("--min-trade-price", type=float, default=0.05)
    return p.parse_args()


def side_cost_bps(price: float) -> float:
    if price < 1:
        return 75.0
    if price < 5:
        return 35.0
    if price < 10:
        return 25.0
    return 15.0


def apply_entry_cost(open_price: float) -> tuple[float, float]:
    bps = side_cost_bps(open_price)
    return open_price * (1.0 + bps / 10_000.0), bps


def apply_exit_cost(raw_exit: float) -> tuple[float, float]:
    bps = side_cost_bps(raw_exit)
    return raw_exit * (1.0 - bps / 10_000.0), bps


def alpaca_fixed_fees(entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> float:
    # Public fee schedule approximation: CAT min $0.01 per day with fills;
    # SEC + FINRA TAF min $0.01 each per day with sales.
    cat_days = 1 if entry_date.date() == exit_date.date() else 2
    return round(cat_days * 0.01 + 0.02, 2)


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr.replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.rolling(period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["SMA20"] = out["Close"].rolling(20).mean()
    out["SMA50"] = out["Close"].rolling(50).mean()
    out["RSI"] = calc_rsi(out["Close"])
    out["ATR"] = calc_atr(out)
    out["ADX"] = calc_adx(out)
    out["VOL_SMA20"] = out["Volume"].rolling(20).mean()
    return out


def normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(-1)
    out = raw.rename(columns={c: str(c).title() for c in raw.columns})
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in out.columns for c in required):
        return pd.DataFrame()
    out = out[required].dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def download_histories(tickers: Iterable[str], start: str, end: str) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    histories: dict[str, pd.DataFrame] = {}
    unavailable: dict[str, str] = {}
    for ticker in tickers:
        try:
            raw = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
            hist = normalize_history(raw)
            if hist.empty or len(hist) < 80:
                unavailable[ticker] = f"sin_barras_suficientes:{len(hist)}"
                continue
            histories[ticker] = enrich(hist)
        except Exception as exc:  # pragma: no cover
            unavailable[ticker] = f"{type(exc).__name__}:{str(exc)[:120]}"
    return histories, unavailable


def quick_tech_signal(row: pd.Series, prev5_close: float | None) -> dict | None:
    close = float(row["Close"])
    prev_close = float(row["_prev_close"]) if row["_prev_close"] else 0.0
    sma20 = float(row["SMA20"]) if not pd.isna(row["SMA20"]) else math.nan
    sma50 = float(row["SMA50"]) if not pd.isna(row["SMA50"]) else math.nan
    rsi = float(row["RSI"]) if not pd.isna(row["RSI"]) else 50.0
    vol_sma = float(row["VOL_SMA20"]) if not pd.isna(row["VOL_SMA20"]) and row["VOL_SMA20"] else 0.0
    vol_spike = float(row["Volume"]) / vol_sma if vol_sma > 0 else 1.0
    chg1 = (close / prev_close - 1.0) * 100.0 if prev_close else 0.0
    chg5 = (close / prev5_close - 1.0) * 100.0 if prev5_close else 0.0
    above_sma20 = close >= sma20 * 0.98 if not math.isnan(sma20) else False
    above_sma50 = close >= sma50 if not math.isnan(sma50) else False

    if chg5 <= -12 or rsi >= 78 or (rsi <= 22 and chg5 < 0):
        return None

    score = 20.0 + max(-10, min(15, chg5)) + max(-5, min(8, chg1))
    if 40 <= rsi <= 65:
        score += 12
    elif 30 <= rsi < 40 or 65 < rsi <= 72:
        score += 4
    if above_sma20:
        score += 8
    if vol_spike >= 1.4:
        score += 6
    if score < 22 or not (chg5 > 0 or above_sma20 or vol_spike >= 1.5):
        return None

    extension = (close / sma20 - 1.0) * 100.0 if sma20 and not math.isnan(sma20) else None
    adx = None if pd.isna(row["ADX"]) else float(row["ADX"])
    return {
        "score": round(score, 4),
        "rsi": round(rsi, 4),
        "chg5_pct": round(chg5, 4),
        "vol_spike": round(vol_spike, 4),
        "adx": None if adx is None else round(adx, 4),
        "atr": None if pd.isna(row["ATR"]) else float(row["ATR"]),
        "above_sma50": bool(above_sma50),
        "extension_sma20_pct": None if extension is None else round(extension, 4),
    }


def build_signal_table(histories: dict[str, pd.DataFrame]) -> dict[pd.Timestamp, list[dict]]:
    by_date: dict[pd.Timestamp, list[dict]] = {}
    for symbol, df in histories.items():
        tmp = df.copy()
        tmp["_prev_close"] = tmp["Close"].shift(1)
        for i in range(55, len(tmp) - 1):
            sig = quick_tech_signal(tmp.iloc[i], float(tmp["Close"].iloc[i - 5]))
            if not sig:
                continue
            sig.update({"symbol": symbol, "signal_date": tmp.index[i], "next_date": tmp.index[i + 1]})
            by_date.setdefault(tmp.index[i], []).append(sig)
    for sigs in by_date.values():
        sigs.sort(key=lambda x: (-float(x["score"]), str(x["symbol"])))
    return by_date


def bar_for(df: pd.DataFrame, when: pd.Timestamp) -> pd.Series | None:
    return df.loc[when] if when in df.index else None


def next_bar_date(df: pd.DataFrame, after: pd.Timestamp) -> pd.Timestamp | None:
    loc = df.index.searchsorted(after, side="right")
    return None if loc >= len(df.index) else df.index[loc]


def risk_qty(equity: float, raw_open: float, *, base: float) -> tuple[int, float, float]:
    brake = base * PROP_BRAKE_PCT
    cushion = max(0.0, equity - brake)
    line_by_cap = 0.25 * min(equity, base)
    line_by_cushion = 0.5 * cushion / STOP_PCT if STOP_PCT > 0 else 0.0
    line_max = max(0.0, min(line_by_cap, line_by_cushion))
    qty = int(line_max // raw_open) if raw_open > 0 else 0
    notional = qty * raw_open
    if qty < 1 or notional < MIN_NOTIONAL_WHOLE:
        return 0, 0.0, line_max
    return qty, notional, line_max


def passes_variant(sig: dict, variant: Variant) -> bool:
    if not variant.trend_filter:
        return True
    adx = sig.get("adx")
    return bool((adx is not None and float(adx) > 25.0) or sig.get("above_sma50"))


def simulate_variant(
    variant: Variant,
    histories: dict[str, pd.DataFrame],
    signals: dict[pd.Timestamp, list[dict]],
    *,
    sample: str,
    capital: float,
    base: float,
    min_trade_price: float,
    start_entries_at: pd.Timestamp | None = None,
    end_entries_before: pd.Timestamp | None = None,
) -> list[Trade]:
    equity = float(capital)
    proposed_brake = base * PROP_BRAKE_PCT
    date_set = sorted({idx for df in histories.values() for idx in df.index})
    trades: list[Trade] = []
    open_state: dict | None = None
    pending: dict | None = None
    symbol_cooldown: dict[str, int] = {}
    global_cooldown = 0

    for current_date in date_set:
        symbol_cooldown = {s: max(0, n - 1) for s, n in symbol_cooldown.items() if n > 1}
        if global_cooldown > 0:
            global_cooldown -= 1

        if pending and pending["entry_date"] <= current_date and open_state is None:
            symbol = pending["symbol"]
            entry_bar = bar_for(histories[symbol], current_date)
            if entry_bar is not None:
                raw_open = float(entry_bar["Open"])
                qty, raw_notional, line_max = risk_qty(equity, raw_open, base=base)
                if raw_open >= min_trade_price and qty >= 1:
                    entry, entry_bps = apply_entry_cost(raw_open)
                    notional = qty * entry
                    atr = pending.get("atr")
                    open_state = {
                        **pending,
                        "entry_date": current_date,
                        "entry": entry,
                        "qty": qty,
                        "notional": notional,
                        "entry_bps": entry_bps,
                        "stop": entry * (1.0 - STOP_PCT),
                        "target": entry * (1.0 + TARGET_PCT),
                        "risk_dollars": entry * STOP_PCT,
                        "peak": entry,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "line_max": line_max,
                        "atr_pct_at_entry": (atr / entry * 100.0) if atr and entry > 0 else None,
                        "tp_atr_multiple": (entry * TARGET_PCT / atr) if atr and atr > 0 else None,
                    }
            pending = None

        if open_state:
            symbol = open_state["symbol"]
            row = bar_for(histories[symbol], current_date)
            if row is None:
                continue
            o, h, l, c = (float(row[x]) for x in ("Open", "High", "Low", "Close"))
            entry = float(open_state["entry"])
            risk_dollars = float(open_state["risk_dollars"])
            prev_peak = float(open_state["peak"])
            trail_armed = prev_peak >= entry * (1.0 + TRAIL_ARM_PCT)
            effective_stop = float(open_state["stop"])
            if trail_armed:
                effective_stop = max(effective_stop, prev_peak * (1.0 - TRAILING_PCT))

            open_state["mfe"] = max(open_state["mfe"], (h - entry) / risk_dollars)
            open_state["mae"] = min(open_state["mae"], (l - entry) / risk_dollars)

            exit_raw = None
            exit_reason = ""
            stop_gap = False
            tp_gap = False
            if o <= effective_stop:
                exit_raw = o
                exit_reason = "stop_gap" if effective_stop <= open_state["stop"] else "stop_trailing_gap"
                stop_gap = True
            elif o >= open_state["target"]:
                exit_raw = o
                exit_reason = "take_profit_gap"
                tp_gap = True
            elif l <= effective_stop:
                exit_raw = effective_stop
                exit_reason = "stop_trailing" if effective_stop > open_state["stop"] else "stop"
            elif h >= open_state["target"]:
                exit_raw = float(open_state["target"])
                exit_reason = "take_profit"

            age_days = (current_date - open_state["entry_date"]).days
            close_pnl_r = (c - entry) / risk_dollars
            if exit_raw is None and variant.eod_green_harvest and c > entry:
                exit_raw = c
                exit_reason = "asegurar_ganancia"
            if exit_raw is None and variant.symmetric_overnight and close_pnl_r < -0.5:
                exit_raw = c
                exit_reason = "eod_risk_cut"
            if exit_raw is None and age_days >= 7 and close_pnl_r <= -0.1:
                nd = next_bar_date(histories[symbol], current_date)
                if nd is not None:
                    exit_raw = float(histories[symbol].loc[nd]["Open"])
                    current_date = nd
                    exit_reason = "time_stop"

            if exit_raw is not None:
                exit_price, _ = apply_exit_cost(float(exit_raw))
                fee = alpaca_fixed_fees(open_state["entry_date"], current_date)
                qty = int(open_state["qty"])
                gross_pnl_usd = (exit_price - entry) * qty
                gross_r = (exit_price / entry - 1.0) / STOP_PCT
                pnl_usd = gross_pnl_usd - fee
                notional = float(open_state["notional"])
                pnl_pct = pnl_usd / notional * 100.0 if notional > 0 else 0.0
                r_mult = pnl_pct / (STOP_PCT * 100.0)
                equity_before = equity
                equity_after = equity + pnl_usd
                proposed_hit = equity_after <= proposed_brake
                session_hit = pnl_usd / equity_before <= -0.05 if equity_before > 0 else False
                trades.append(
                    Trade(
                        variant=variant.name,
                        sample=sample,
                        symbol=symbol,
                        signal_date=open_state["signal_date"].date().isoformat(),
                        entry_date=open_state["entry_date"].date().isoformat(),
                        exit_date=current_date.date().isoformat(),
                        qty=qty,
                        entry=round(entry, 4),
                        exit=round(exit_price, 4),
                        notional_usd=round(notional, 4),
                        equity_before=round(equity_before, 4),
                        equity_after=round(equity_after, 4),
                        fee_usd=fee,
                        pnl_usd=round(pnl_usd, 4),
                        pnl_pct=round(pnl_pct, 4),
                        r_multiple=round(r_mult, 4),
                        gross_r_multiple=round(gross_r, 4),
                        stop=round(open_state["stop"], 4),
                        target=round(open_state["target"], 4),
                        mfe_r=round(open_state["mfe"], 4),
                        mae_r=round(open_state["mae"], 4),
                        duration_days=int((current_date - open_state["entry_date"]).days),
                        exit_reason=exit_reason,
                        stop_gap=stop_gap,
                        tp_gap=tp_gap,
                        proposed_brake_after_trade=proposed_hit,
                        session_brake_after_trade=session_hit,
                        atr_pct_at_entry=None if open_state["atr_pct_at_entry"] is None else round(open_state["atr_pct_at_entry"], 4),
                        tp_atr_multiple=None if open_state["tp_atr_multiple"] is None else round(open_state["tp_atr_multiple"], 4),
                        signal_score=round(float(open_state["score"]), 4),
                        signal_rsi=open_state.get("rsi"),
                        signal_chg5_pct=open_state.get("chg5_pct"),
                        signal_vol_spike=open_state.get("vol_spike"),
                        signal_adx=open_state.get("adx"),
                        signal_extension_sma20_pct=open_state.get("extension_sma20_pct"),
                    )
                )
                equity = equity_after
                is_stop = "stop" in exit_reason
                if is_stop:
                    symbol_cooldown[symbol] = max(symbol_cooldown.get(symbol, 0), variant.cooldown_same_symbol_sessions)
                    if r_mult <= -1.0:
                        global_cooldown = max(global_cooldown, variant.cooldown_global_after_stop_sessions)
                open_state = None
                pending = None
                continue

            open_state["peak"] = max(prev_peak, h)

        in_entry_window = start_entries_at is None or current_date >= start_entries_at
        if end_entries_before is not None and current_date >= end_entries_before:
            in_entry_window = False
        if open_state is None and pending is None and current_date in signals and in_entry_window:
            if equity <= proposed_brake or global_cooldown > 0:
                continue
            candidates = []
            for sig in signals[current_date]:
                if symbol_cooldown.get(sig["symbol"], 0) > 0 or not passes_variant(sig, variant):
                    continue
                row = bar_for(histories[sig["symbol"]], sig["next_date"])
                if row is None or float(row["Open"]) < min_trade_price:
                    continue
                qty, _, _ = risk_qty(equity, float(row["Open"]), base=base)
                if qty >= 1:
                    candidates.append(sig)
            if candidates:
                chosen = candidates[0]
                pending = {**chosen, "entry_date": chosen["next_date"]}

    return trades


def account_drawdown_pct(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    curve = pd.concat([trades["equity_before"].iloc[[0]], trades["equity_after"]], ignore_index=True)
    dd = curve / curve.cummax() - 1.0
    return round(float(dd.min() * 100.0), 4)


def worst_losing_streak(trades: pd.DataFrame) -> dict:
    worst = {"count": 0, "usd": 0.0, "pct": 0.0}
    count = 0
    usd = 0.0
    start_eq = None
    end_eq = None
    for row in trades.itertuples(index=False):
        pnl = float(getattr(row, "pnl_usd", 0.0) or 0.0)
        if pnl < 0:
            if count == 0:
                start_eq = float(row.equity_before)
            count += 1
            usd += pnl
            end_eq = float(row.equity_after)
            pct = (end_eq / start_eq - 1.0) * 100.0 if start_eq else 0.0
            if count > worst["count"] or (count == worst["count"] and usd < worst["usd"]):
                worst = {"count": count, "usd": usd, "pct": pct}
        else:
            count = 0
            usd = 0.0
            start_eq = None
            end_eq = None
    return {
        "worst_loss_streak_trades": int(worst["count"]),
        "worst_loss_streak_usd": round(float(worst["usd"]), 4),
        "worst_loss_streak_pct": round(float(worst["pct"]), 4),
    }


def stats_ci(trades: pd.DataFrame) -> dict:
    n = int(len(trades))
    if n == 0:
        return {"expectancy_r_ci95_low": None, "expectancy_r_ci95_high": None, "expectancy_r_ci90_lower_one_sided": None, "n_for_0p2r_90": None, "std_r": None}
    vals = trades["r_multiple"].astype(float)
    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if n > 1 else 0.0
    sem = std / math.sqrt(n) if n > 1 else 0.0
    n_req = math.ceil((1.2816 * std / 0.2) ** 2) if std > 0 else 1
    return {
        "std_r": round(std, 4),
        "expectancy_r_ci95_low": round(mean - 1.96 * sem, 4),
        "expectancy_r_ci95_high": round(mean + 1.96 * sem, 4),
        "expectancy_r_ci90_lower_one_sided": round(mean - 1.2816 * sem, 4),
        "n_for_0p2r_90": int(n_req),
    }


def metrics_for(trades: pd.DataFrame, *, proposed_brake: float) -> dict:
    if trades.empty:
        return {
            "trades": 0, "win_rate_pct": 0.0, "expectancy_r_net": 0.0, "expectancy_r_gross": 0.0,
            "expectancy_pct_net": 0.0, "profit_factor_net": 0.0, "account_drawdown_pct": 0.0,
            "final_equity": None, "account_return_pct": None, "proposed_brake_triggered": False,
            "proposed_brake_date": None, "session_brake_triggered": False, "session_brake_date": None,
            "stop_gap_count": 0, "tp_gap_count": 0, "fees_usd": 0.0, "exits": {},
            **worst_losing_streak(trades), **stats_ci(trades),
        }
    wins = trades[trades["pnl_usd"] > 0]
    losses = trades[trades["pnl_usd"] < 0]
    gross_win = float(wins["pnl_usd"].sum())
    gross_loss = abs(float(losses["pnl_usd"].sum()))
    prop_rows = trades[trades["equity_after"] <= proposed_brake]
    sess_rows = trades[trades["session_brake_after_trade"]]
    first_equity = float(trades["equity_before"].iloc[0])
    final_equity = float(trades["equity_after"].iloc[-1])
    return {
        "trades": int(len(trades)),
        "win_rate_pct": round(float((trades["pnl_usd"] > 0).mean() * 100.0), 2),
        "expectancy_r_net": round(float(trades["r_multiple"].mean()), 4),
        "expectancy_r_gross": round(float(trades["gross_r_multiple"].mean()), 4),
        "expectancy_pct_net": round(float(trades["pnl_pct"].mean()), 4),
        "profit_factor_net": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "account_drawdown_pct": account_drawdown_pct(trades),
        "final_equity": round(final_equity, 4),
        "account_return_pct": round((final_equity / first_equity - 1.0) * 100.0, 4),
        "proposed_brake_triggered": not prop_rows.empty,
        "proposed_brake_date": None if prop_rows.empty else str(prop_rows.iloc[0]["exit_date"]),
        "session_brake_triggered": not sess_rows.empty,
        "session_brake_date": None if sess_rows.empty else str(sess_rows.iloc[0]["exit_date"]),
        "stop_gap_count": int(trades["stop_gap"].sum()),
        "tp_gap_count": int(trades["tp_gap"].sum()),
        "fees_usd": round(float(trades["fee_usd"].sum()), 4),
        "median_mfe_r": round(float(trades["mfe_r"].median()), 4),
        "median_mae_r": round(float(trades["mae_r"].median()), 4),
        "median_tp_atr_multiple": None if trades["tp_atr_multiple"].dropna().empty else round(float(trades["tp_atr_multiple"].dropna().median()), 4),
        **worst_losing_streak(trades),
        **stats_ci(trades),
        "exits": trades["exit_reason"].value_counts().to_dict(),
    }


def summarize(trades: pd.DataFrame, *, proposed_brake: float) -> pd.DataFrame:
    rows = []
    if trades.empty:
        return pd.DataFrame()
    for (sample, variant), group in trades.groupby(["sample", "variant"]):
        rows.append({"sample": sample, "variant": variant, **metrics_for(group, proposed_brake=proposed_brake)})
    return pd.DataFrame(rows).sort_values(["variant", "sample"])


def choose_split_date(histories: dict[str, pd.DataFrame], explicit: str) -> str | None:
    if explicit:
        return explicit
    all_dates = sorted({idx for df in histories.values() for idx in df.index})
    if len(all_dates) < 120:
        return None
    return all_dates[int(len(all_dates) * 0.70)].date().isoformat()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    universe = [t.strip().upper() for t in args.universe.split(",") if t.strip()] or list(DEFAULT_UNIVERSE)
    if args.include_legacy:
        universe = list(dict.fromkeys([*universe, *LEGACY_OR_DELISTED]))

    histories, unavailable = download_histories(universe, args.start, args.end)
    signals = build_signal_table(histories)
    split_date = choose_split_date(histories, args.split_date)
    split_ts = pd.Timestamp(split_date) if split_date else None
    proposed_brake = args.base * PROP_BRAKE_PCT

    all_trades: list[Trade] = []
    for variant in VARIANTS:
        all_trades.extend(simulate_variant(variant, histories, signals, sample="in_sample", capital=args.capital, base=args.base, min_trade_price=args.min_trade_price, end_entries_before=split_ts))
        if split_ts is not None:
            all_trades.extend(simulate_variant(variant, histories, signals, sample="out_of_sample", capital=args.capital, base=args.base, min_trade_price=args.min_trade_price, start_entries_at=split_ts))

    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    if trades_df.empty:
        trades_df = pd.DataFrame(columns=[field for field in Trade.__dataclass_fields__])
    metrics_df = summarize(trades_df, proposed_brake=proposed_brake)

    trades_path = out_dir / "trades.csv"
    metrics_path = out_dir / "metrics.csv"
    meta_path = out_dir / "metadata.json"
    trades_df.to_csv(trades_path, index=False)
    metrics_df.to_csv(metrics_path, index=False)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "start": args.start,
        "end": args.end,
        "split_date": split_date,
        "capital": args.capital,
        "base": args.base,
        "proposed_accumulated_brake_usd": proposed_brake,
        "prop_brake_note": "Proposed research brake at 95% of base; not the current LIVE session-loss gate.",
        "sizing": "whole shares; max line = min(25%*min(equity, base), 0.5*cushion/stop_pct); cushion=equity-95%*base; no trade if qty<1 or notional<$3",
        "fractional_note": "Alpaca fractional orders are day-only and do not support bracket/OCO protection; not used.",
        "universe_requested": universe,
        "tickers_with_data": sorted(histories),
        "unavailable": unavailable,
        "variants": [asdict(v) for v in VARIANTS],
        "cost_model": {
            "spread_slippage_per_side_bps": {"price_lt_1": 75, "price_1_to_5": 35, "price_5_to_10": 25, "price_gte_10": 15},
            "fixed_fees": "CAT $0.01 per day with fills + SEC $0.01 and FINRA TAF $0.01 per day with sales, rounded daily minimums; sequential trades approximate sharing by date.",
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {trades_path}")
    print(f"wrote {metrics_path}")
    print(f"wrote {meta_path}")
    if not metrics_df.empty:
        print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
