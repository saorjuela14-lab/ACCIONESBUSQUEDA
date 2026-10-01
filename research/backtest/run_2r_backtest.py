"""Backtest Monarch Capital's ultra-micro 2R exit policy.

This is a research-only script. It does not import live execution services, does
not read secrets, and never sends orders. Signals are computed from closed daily
bars and entered on the next available open.
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


DEFAULT_UNIVERSE = (
    "PLUG",
    "BITF",
    "AMC",
    "BBAI",
    "ABEV",
    "OPEN",
    "CLOV",
    "SNAP",
    "NIO",
    "SENS",
    "SOUN",
    "BB",
    "NOK",
    "F",
    "AAL",
    "SOFI",
    "SIRI",
    "T",
    "VZ",
    "PFE",
    "INTC",
    "KEY",
    "HBAN",
    "RF",
    "WBD",
    "PCG",
    "KMI",
    "AGNC",
    "NLY",
    "FCEL",
    "RIOT",
    "MARA",
    "ACHR",
    "JOBY",
    "LUNR",
    "ASTS",
    "CIFR",
    "APLD",
    "GRAB",
    "CHPT",
    "SPCE",
    "DNA",
    "MVST",
    "LAZR",
    "VALE",
    "ITUB",
    "PBR",
    "BBD",
    "GOLD",
    "NU",
    "CFG",
    "JD",
    "BIDU",
    "XPEV",
    "LI",
    "HOOD",
    "UPST",
    "PATH",
    "RIG",
    "HAL",
    "HL",
    "AG",
    "CDE",
    "BTBT",
    "CAN",
    "HUT",
    "CLSK",
    "WULF",
    "IREN",
    "IONQ",
    "RXRX",
    "ARR",
    "TWO",
    "ORC",
    "MPW",
)

# Names that appeared in project history but are not in the current live seed
# list because they are stale/delisted or otherwise problematic.
LEGACY_OR_DELISTED = ("NKLA", "WISH", "BBIG")


@dataclass(frozen=True)
class Variant:
    name: str
    stop_pct: float = 0.08
    target_pct: float = 0.16
    trail_arm_pct: float = 0.05
    trailing_pct: float = 0.10
    stagnation_days: int = 2
    stagnation_min_pnl_pct: float = 1.5
    time_stop_days: int = 7
    break_even_after_r: float | None = None
    require_adx: float | None = None


VARIANTS = (
    Variant(name="actual_2R"),
    Variant(name="tp_1_5R", target_pct=0.12),
    Variant(name="be_tras_1R", break_even_after_r=1.0),
    Variant(name="adx25_2R", require_adx=25.0),
    Variant(name="stagnation_4d", stagnation_days=4),
)


@dataclass
class Trade:
    variant: str
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    qty: float
    notional_usd: float
    equity_before: float
    equity_after: float
    pnl_usd: float
    stop: float
    target: float
    risk_pct: float
    target_pct: float
    pnl_pct: float
    r_multiple: float
    mfe_r: float
    mae_r: float
    duration_days: int
    exit_reason: str
    atr_pct_at_entry: float | None
    tp_atr_multiple: float | None
    entry_cost_bps: float
    exit_cost_bps: float
    signal_score: float
    signal_rsi: float | None
    signal_chg5_pct: float | None
    signal_vol_spike: float | None
    signal_adx: float | None
    signal_extension_sma20_pct: float | None
    kill_switch_after_trade: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-01-01", help="First date for public bars.")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date, exclusive.")
    parser.add_argument("--capital", type=float, default=21.76, help="Initial account equity.")
    parser.add_argument("--min-notional", type=float, default=5.0)
    parser.add_argument("--max-position-pct", type=float, default=0.35)
    parser.add_argument("--max-notional", type=float, default=25.0)
    parser.add_argument("--kill-dd-pct", type=float, default=5.0)
    parser.add_argument("--min-trade-price", type=float, default=0.05)
    parser.add_argument("--split-date", default="", help="Optional OOS split date YYYY-MM-DD.")
    parser.add_argument("--out-dir", default="research/backtest/output")
    parser.add_argument("--universe", default="", help="Comma-separated override universe.")
    parser.add_argument("--include-legacy", action="store_true")
    return parser.parse_args()


def side_cost_bps(price: float) -> float:
    """Approximate half-spread + slippage for tiny market orders."""
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
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr.replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.rolling(period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["SMA20"] = out["Close"].rolling(20).mean()
    out["RSI"] = calc_rsi(out["Close"])
    out["ATR"] = calc_atr(out)
    out["ADX"] = calc_adx(out)
    out["VOL_SMA20"] = out["Volume"].rolling(20).mean()
    return out


def normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        # yfinance may return a multi-index for some error cases; this script
        # downloads per ticker, so keep the first level if present.
        raw.columns = raw.columns.get_level_values(-1)
    out = raw.rename(columns={c: str(c).title() for c in raw.columns})
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        return pd.DataFrame()
    out = out[required].dropna()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def download_histories(
    tickers: Iterable[str], start: str, end: str
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    histories: dict[str, pd.DataFrame] = {}
    unavailable: dict[str, str] = {}
    for ticker in tickers:
        try:
            # Adjusted OHLC avoids false 10x/100x wins or losses around splits and
            # reverse splits, common in this universe.
            raw = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
            hist = normalize_history(raw)
            if hist.empty or len(hist) < 60:
                unavailable[ticker] = f"sin_barras_suficientes:{len(hist)}"
                continue
            histories[ticker] = enrich(hist)
        except Exception as exc:  # pragma: no cover - network/provider dependent
            unavailable[ticker] = f"{type(exc).__name__}:{str(exc)[:120]}"
    return histories, unavailable


def quick_tech_signal(row: pd.Series, prev5_close: float | None) -> dict | None:
    close = float(row["Close"])
    sma20 = float(row["SMA20"]) if not pd.isna(row["SMA20"]) else math.nan
    rsi = float(row["RSI"]) if not pd.isna(row["RSI"]) else 50.0
    vol_sma = float(row["VOL_SMA20"]) if not pd.isna(row["VOL_SMA20"]) and row["VOL_SMA20"] else 0.0
    vol_spike = float(row["Volume"]) / vol_sma if vol_sma > 0 else 1.0
    chg1 = float(close / row["_prev_close"] - 1.0) * 100.0 if row["_prev_close"] else 0.0
    chg5 = float(close / prev5_close - 1.0) * 100.0 if prev5_close else 0.0
    above_sma = close >= sma20 * 0.98 if not math.isnan(sma20) else False

    if chg5 <= -12:
        return None
    if rsi >= 78:
        return None
    if rsi <= 22 and chg5 < 0:
        return None

    score = 20.0
    score += max(-10, min(15, chg5))
    score += max(-5, min(8, chg1))
    if 40 <= rsi <= 65:
        score += 12
    elif 30 <= rsi < 40 or 65 < rsi <= 72:
        score += 4
    if above_sma:
        score += 8
    if vol_spike >= 1.4:
        score += 6
    ok = score >= 22 and (chg5 > 0 or above_sma or vol_spike >= 1.5)
    if not ok:
        return None
    extension = (close / sma20 - 1.0) * 100.0 if sma20 and not math.isnan(sma20) else None
    return {
        "score": round(score, 4),
        "rsi": round(rsi, 4),
        "chg5_pct": round(chg5, 4),
        "vol_spike": round(vol_spike, 4),
        "adx": None if pd.isna(row["ADX"]) else round(float(row["ADX"]), 4),
        "atr": None if pd.isna(row["ATR"]) else float(row["ATR"]),
        "extension_sma20_pct": None if extension is None else round(extension, 4),
    }


def build_signal_table(histories: dict[str, pd.DataFrame]) -> dict[pd.Timestamp, list[dict]]:
    by_date: dict[pd.Timestamp, list[dict]] = {}
    for symbol, df in histories.items():
        tmp = df.copy()
        tmp["_prev_close"] = tmp["Close"].shift(1)
        for i in range(25, len(tmp) - 1):
            row = tmp.iloc[i]
            prev5 = float(tmp["Close"].iloc[i - 5]) if i >= 5 else None
            sig = quick_tech_signal(row, prev5)
            if not sig:
                continue
            sig.update({"symbol": symbol, "signal_date": tmp.index[i], "next_date": tmp.index[i + 1]})
            by_date.setdefault(tmp.index[i], []).append(sig)
    for sigs in by_date.values():
        sigs.sort(key=lambda x: (-float(x["score"]), str(x["symbol"])))
    return by_date


def bar_for(df: pd.DataFrame, when: pd.Timestamp) -> pd.Series | None:
    if when not in df.index:
        return None
    return df.loc[when]


def next_bar_date(df: pd.DataFrame, after: pd.Timestamp) -> pd.Timestamp | None:
    loc = df.index.searchsorted(after, side="right")
    if loc >= len(df.index):
        return None
    return df.index[loc]


def simulate_variant(
    variant: Variant,
    histories: dict[str, pd.DataFrame],
    signals: dict[pd.Timestamp, list[dict]],
    *,
    capital: float,
    min_notional: float,
    max_position_pct: float,
    max_notional: float,
    kill_dd_pct: float,
    min_trade_price: float,
    start_entries_at: pd.Timestamp | None = None,
    stop_on_kill: bool = False,
) -> list[Trade]:
    all_dates = sorted(signals)
    trades: list[Trade] = []
    equity = float(capital)
    kill_level = capital * (1.0 - kill_dd_pct / 100.0)
    killed = False
    open_state: dict | None = None
    pending: dict | None = None

    date_set = sorted({idx for df in histories.values() for idx in df.index})
    for current_date in date_set:
        if killed and stop_on_kill:
            break
        if pending and pending["entry_date"] <= current_date and open_state is None:
            symbol = pending["symbol"]
            df = histories[symbol]
            entry_bar = bar_for(df, current_date)
            if entry_bar is not None:
                raw_open = float(entry_bar["Open"])
                max_line = min(max_notional, equity * max_position_pct)
                if raw_open >= min_trade_price and max_line >= min_notional:
                    notional = max(min_notional, max_line)
                    entry, entry_bps = apply_entry_cost(raw_open)
                    risk_dollars = entry * variant.stop_pct
                    atr = pending.get("atr")
                    open_state = {
                        **pending,
                        "entry_date": current_date,
                        "entry": entry,
                        "equity_before": equity,
                        "notional_usd": notional,
                        "qty": notional / entry,
                        "entry_cost_bps": entry_bps,
                        "stop": entry * (1.0 - variant.stop_pct),
                        "target": entry * (1.0 + variant.target_pct),
                        "risk_dollars": risk_dollars,
                        "peak": entry,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "atr_pct_at_entry": (atr / entry * 100.0) if atr and entry > 0 else None,
                        "tp_atr_multiple": (entry * variant.target_pct / atr) if atr and atr > 0 else None,
                    }
            pending = None

        if open_state:
            symbol = open_state["symbol"]
            df = histories[symbol]
            row = bar_for(df, current_date)
            if row is None:
                continue
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
            open_state["peak"] = max(open_state["peak"], high)
            risk_dollars = float(open_state["risk_dollars"])
            open_state["mfe"] = max(open_state["mfe"], (high - open_state["entry"]) / risk_dollars)
            open_state["mae"] = min(open_state["mae"], (low - open_state["entry"]) / risk_dollars)

            effective_stop = float(open_state["stop"])
            if (
                variant.break_even_after_r is not None
                and open_state["mfe"] >= variant.break_even_after_r
            ):
                effective_stop = max(effective_stop, float(open_state["entry"]))
            if open_state["peak"] >= open_state["entry"] * (1.0 + variant.trail_arm_pct):
                trail_stop = open_state["peak"] * (1.0 - variant.trailing_pct)
                effective_stop = max(effective_stop, trail_stop)

            exit_raw = None
            exit_reason = ""
            # Conservative tie-breaker: if daily OHLC touches stop and TP, assume stop first.
            if low <= effective_stop:
                exit_raw = effective_stop
                exit_reason = "stop_trailing" if effective_stop > open_state["stop"] else "stop"
            elif high >= open_state["target"]:
                exit_raw = float(open_state["target"])
                exit_reason = "take_profit"

            age_days = (current_date - open_state["entry_date"]).days
            pnl_close_pct = (close / open_state["entry"] - 1.0) * 100.0
            next_date = next_bar_date(df, current_date)
            if exit_raw is None and age_days >= variant.stagnation_days:
                if 0.0 <= pnl_close_pct < variant.stagnation_min_pnl_pct and next_date is not None:
                    next_row = df.loc[next_date]
                    exit_raw = float(next_row["Open"])
                    current_date = next_date
                    exit_reason = "stagnation"
            if exit_raw is None and age_days >= variant.time_stop_days:
                if pnl_close_pct <= -0.5 and next_date is not None:
                    next_row = df.loc[next_date]
                    exit_raw = float(next_row["Open"])
                    current_date = next_date
                    exit_reason = "time_stop"

            if exit_raw is not None:
                exit_price, exit_bps = apply_exit_cost(float(exit_raw))
                pnl_pct = (exit_price / open_state["entry"] - 1.0) * 100.0
                pnl_usd = float(open_state["notional_usd"]) * pnl_pct / 100.0
                equity_after = equity + pnl_usd
                kill_after = equity_after <= kill_level
                risk_pct = variant.stop_pct * 100.0
                trades.append(
                    Trade(
                        variant=variant.name,
                        symbol=symbol,
                        signal_date=open_state["signal_date"].date().isoformat(),
                        entry_date=open_state["entry_date"].date().isoformat(),
                        exit_date=current_date.date().isoformat(),
                        entry=round(open_state["entry"], 4),
                        exit=round(exit_price, 4),
                        qty=round(float(open_state["qty"]), 6),
                        notional_usd=round(float(open_state["notional_usd"]), 4),
                        equity_before=round(equity, 4),
                        equity_after=round(equity_after, 4),
                        pnl_usd=round(pnl_usd, 4),
                        stop=round(open_state["stop"], 4),
                        target=round(open_state["target"], 4),
                        risk_pct=round(risk_pct, 4),
                        target_pct=round(variant.target_pct * 100.0, 4),
                        pnl_pct=round(pnl_pct, 4),
                        r_multiple=round(pnl_pct / risk_pct, 4),
                        mfe_r=round(open_state["mfe"], 4),
                        mae_r=round(open_state["mae"], 4),
                        duration_days=int((current_date - open_state["entry_date"]).days),
                        exit_reason=exit_reason,
                        atr_pct_at_entry=(
                            None
                            if open_state["atr_pct_at_entry"] is None
                            else round(open_state["atr_pct_at_entry"], 4)
                        ),
                        tp_atr_multiple=(
                            None
                            if open_state["tp_atr_multiple"] is None
                            else round(open_state["tp_atr_multiple"], 4)
                        ),
                        entry_cost_bps=round(float(open_state["entry_cost_bps"]), 2),
                        exit_cost_bps=round(float(exit_bps), 2),
                        signal_score=round(float(open_state["score"]), 4),
                        signal_rsi=open_state.get("rsi"),
                        signal_chg5_pct=open_state.get("chg5_pct"),
                        signal_vol_spike=open_state.get("vol_spike"),
                        signal_adx=open_state.get("adx"),
                        signal_extension_sma20_pct=open_state.get("extension_sma20_pct"),
                        kill_switch_after_trade=kill_after,
                    )
                )
                equity = equity_after
                if kill_after:
                    killed = True
                open_state = None
                pending = None
                continue

        if (
            open_state is None
            and pending is None
            and current_date in signals
            and (start_entries_at is None or current_date >= start_entries_at)
        ):
            candidates = []
            for sig in signals[current_date]:
                if variant.require_adx is not None:
                    adx = sig.get("adx")
                    if adx is None or float(adx) < variant.require_adx:
                        continue
                next_date = sig["next_date"]
                row = bar_for(histories[sig["symbol"]], next_date)
                if row is None:
                    continue
                if float(row["Open"]) >= min_trade_price:
                    candidates.append(sig)
            if candidates:
                chosen = candidates[0]
                pending = {**chosen, "entry_date": chosen["next_date"]}

    return trades


def max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    curve = (1.0 + series / 100.0).cumprod()
    dd = curve / curve.cummax() - 1.0
    return float(dd.min() * 100.0)


def worst_losing_streak(trades: pd.DataFrame) -> dict:
    worst = {"count": 0, "usd": 0.0, "pct": 0.0}
    cur_count = 0
    cur_usd = 0.0
    start_equity = None
    end_equity = None
    for row in trades.itertuples(index=False):
        pnl = float(getattr(row, "pnl_usd", 0.0) or 0.0)
        if pnl < 0:
            if cur_count == 0:
                start_equity = float(getattr(row, "equity_before", 0.0) or 0.0)
            cur_count += 1
            cur_usd += pnl
            end_equity = float(getattr(row, "equity_after", 0.0) or 0.0)
            pct = (
                (end_equity / start_equity - 1.0) * 100.0
                if start_equity and start_equity > 0 and end_equity is not None
                else 0.0
            )
            if cur_count > worst["count"] or (
                cur_count == worst["count"] and cur_usd < worst["usd"]
            ):
                worst = {"count": cur_count, "usd": cur_usd, "pct": pct}
        else:
            cur_count = 0
            cur_usd = 0.0
            start_equity = None
            end_equity = None
    return {
        "worst_loss_streak_trades": int(worst["count"]),
        "worst_loss_streak_usd": round(float(worst["usd"]), 4),
        "worst_loss_streak_pct": round(float(worst["pct"]), 4),
    }


def metrics_for(trades: pd.DataFrame, *, kill_level: float | None = None) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "expectancy_r": 0.0,
            "expectancy_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "median_mfe_r": None,
            "median_mae_r": None,
            "median_tp_atr_multiple": None,
            "final_equity": None,
            "account_return_pct": None,
            "kill_switch_triggered": False,
            "kill_switch_date": None,
            "worst_loss_streak_trades": 0,
            "worst_loss_streak_usd": 0.0,
            "worst_loss_streak_pct": 0.0,
            "exits": {},
        }
    wins = trades[trades["pnl_pct"] > 0]
    losses = trades[trades["pnl_pct"] < 0]
    gross_win = float(wins["pnl_pct"].sum())
    gross_loss = abs(float(losses["pnl_pct"].sum()))
    kill_rows = (
        trades[trades["equity_after"] <= kill_level]
        if kill_level is not None and "equity_after" in trades.columns
        else pd.DataFrame()
    )
    first_equity = float(trades["equity_before"].iloc[0])
    final_equity = float(trades["equity_after"].iloc[-1])
    return {
        "trades": int(len(trades)),
        "win_rate_pct": round(float((trades["pnl_pct"] > 0).mean() * 100.0), 2),
        "expectancy_r": round(float(trades["r_multiple"].mean()), 4),
        "expectancy_pct": round(float(trades["pnl_pct"].mean()), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_drawdown(trades["pnl_pct"]), 4),
        "median_mfe_r": round(float(trades["mfe_r"].median()), 4),
        "median_mae_r": round(float(trades["mae_r"].median()), 4),
        "median_tp_atr_multiple": (
            None
            if trades["tp_atr_multiple"].dropna().empty
            else round(float(trades["tp_atr_multiple"].dropna().median()), 4)
        ),
        "final_equity": round(final_equity, 4),
        "account_return_pct": round((final_equity / first_equity - 1.0) * 100.0, 4)
        if first_equity > 0
        else None,
        "kill_switch_triggered": not kill_rows.empty,
        "kill_switch_date": None if kill_rows.empty else str(kill_rows.iloc[0]["exit_date"]),
        **worst_losing_streak(trades),
        "exits": trades["exit_reason"].value_counts().to_dict(),
    }


def summarize(trades: pd.DataFrame, split_date: str | None, *, kill_level: float) -> pd.DataFrame:
    rows = []
    for variant, group in trades.groupby("variant"):
        rows.append({"variant": variant, "sample": "total", **metrics_for(group, kill_level=kill_level)})
        if split_date:
            split_ts = pd.Timestamp(split_date)
            entry_dates = pd.to_datetime(group["entry_date"])
            rows.append(
                {
                    "variant": variant,
                    "sample": "in_sample",
                    **metrics_for(group[entry_dates < split_ts], kill_level=kill_level),
                }
            )
            rows.append(
                {
                    "variant": variant,
                    "sample": "out_of_sample",
                    **metrics_for(group[entry_dates >= split_ts], kill_level=kill_level),
                }
            )
    return pd.DataFrame(rows)


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

    universe = [t.strip().upper() for t in args.universe.split(",") if t.strip()]
    if not universe:
        universe = list(DEFAULT_UNIVERSE)
    if args.include_legacy:
        universe = list(dict.fromkeys([*universe, *LEGACY_OR_DELISTED]))

    histories, unavailable = download_histories(universe, args.start, args.end)
    signals = build_signal_table(histories)

    split_date = choose_split_date(histories, args.split_date)
    all_trades: list[Trade] = []
    for variant in VARIANTS:
        all_trades.extend(
            simulate_variant(
                variant,
                histories,
                signals,
                capital=args.capital,
                min_notional=args.min_notional,
                max_position_pct=args.max_position_pct,
                max_notional=args.max_notional,
                kill_dd_pct=args.kill_dd_pct,
                min_trade_price=args.min_trade_price,
                stop_on_kill=False,
            )
        )

    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    if trades_df.empty:
        trades_df = pd.DataFrame(columns=[field for field in Trade.__dataclass_fields__])
    kill_level = args.capital * (1.0 - args.kill_dd_pct / 100.0)
    metrics_df = (
        summarize(trades_df, split_date, kill_level=kill_level)
        if not trades_df.empty
        else pd.DataFrame()
    )

    trades_path = out_dir / "trades.csv"
    metrics_path = out_dir / "metrics.csv"
    oos_account_trades_path = out_dir / "oos_account_trades.csv"
    oos_account_metrics_path = out_dir / "oos_account_metrics.csv"
    meta_path = out_dir / "metadata.json"
    trades_df.to_csv(trades_path, index=False)
    metrics_df.to_csv(metrics_path, index=False)
    oos_account_df = pd.DataFrame()
    oos_account_metrics_df = pd.DataFrame()
    if split_date:
        oos_trades: list[Trade] = []
        split_ts = pd.Timestamp(split_date)
        for variant in VARIANTS:
            oos_trades.extend(
                simulate_variant(
                    variant,
                    histories,
                    signals,
                    capital=args.capital,
                    min_notional=args.min_notional,
                    max_position_pct=args.max_position_pct,
                    max_notional=args.max_notional,
                    kill_dd_pct=args.kill_dd_pct,
                    min_trade_price=args.min_trade_price,
                    start_entries_at=split_ts,
                    stop_on_kill=True,
                )
            )
        oos_account_df = pd.DataFrame([asdict(t) for t in oos_trades])
        if not oos_account_df.empty:
            oos_account_metrics_df = summarize(
                oos_account_df,
                None,
                kill_level=kill_level,
            )
        oos_account_df.to_csv(oos_account_trades_path, index=False)
        oos_account_metrics_df.to_csv(oos_account_metrics_path, index=False)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "start": args.start,
        "end": args.end,
        "capital": args.capital,
        "min_notional": args.min_notional,
        "max_position_pct": args.max_position_pct,
        "max_notional": args.max_notional,
        "initial_max_line_usd": min(args.max_notional, args.capital * args.max_position_pct),
        "kill_dd_pct": args.kill_dd_pct,
        "kill_level_usd": kill_level,
        "min_trade_price": args.min_trade_price,
        "split_date": split_date,
        "universe_requested": universe,
        "tickers_with_data": sorted(histories),
        "unavailable": unavailable,
        "variants": [asdict(v) for v in VARIANTS],
        "cost_model": {
            "per_side_bps": {
                "price_lt_1": 75,
                "price_1_to_5": 35,
                "price_5_to_10": 25,
                "price_gte_10": 15,
            },
            "description": "half-spread + slippage estimate for tiny market orders; commissions assumed zero",
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    print(f"wrote {trades_path}")
    print(f"wrote {metrics_path}")
    if split_date:
        print(f"wrote {oos_account_trades_path}")
        print(f"wrote {oos_account_metrics_path}")
    print(f"wrote {meta_path}")
    if not metrics_df.empty:
        print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
