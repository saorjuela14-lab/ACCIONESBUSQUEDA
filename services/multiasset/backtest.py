"""Honest daily backtest: next-bar entry, 1×, costs, OOS split, no look-ahead.

Crypto bars include weekends when the source has them; daily loss is calendar-day
(including Sat/Sun). Gold/FX use session days only.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from services.multiasset.signals import (
    LEGACY_TARGET_PCT,
    TradeSignal,
    legacy_crypto_signal,
    legacy_forex_signal,
    legacy_gold_signal,
    new_crypto_signal,
    new_forex_signal,
    new_gold_signal,
)

COST_BPS = {"gold": 8.0, "forex": 10.0, "crypto": 25.0}  # round-trip
RISK_PCT = {"gold": 2.5, "forex": 2.5, "crypto": 3.0}
MAX_DAILY_LOSS_PCT = 6.0
MAX_DD_KILL_PCT = 20.0
START_EQUITY = 10_000.0


@dataclass
class ClosedTrade:
    entry_date: str
    exit_date: str
    side: str
    entry: float
    exit: float
    pnl_pct: float
    pnl_usd: float
    r_multiple: float
    reason: str
    stop_pct: float
    weekend_hold: bool = False


@dataclass
class BacktestReport:
    desk: str
    book: str
    symbol: str
    bars: int
    start: str
    end: str
    leverage: float
    margin: bool
    stop_pct_typical: float
    stop_r: float
    trail_atr_mult: float
    risk_pct: float
    cost_bps_roundtrip: float
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    expectancy_pct: float | None
    profit_factor: float | None
    cagr_pct: float | None
    max_drawdown_pct: float | None
    sharpe: float | None
    total_return_pct: float | None
    worst_losing_streak: int
    worst_losing_streak_usd: float
    kill_switch_fired: bool
    kill_switch_at: str | None
    kill_switch_dd_pct: float | None
    oos_start: str | None
    oos_trades: int
    oos_total_return_pct: float | None
    oos_max_drawdown_pct: float | None
    oos_sharpe: float | None
    oos_win_rate: float | None
    crypto_24_7: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sample_trades: list[dict[str, Any]] = field(default_factory=list)


def _sharpe(rets: list[float]) -> float | None:
    if len(rets) < 20:
        return None
    s = pd.Series(rets)
    sd = float(s.std())
    if sd <= 0:
        return None
    # daily → ann; crypto 365, else 252
    return round(float(s.mean()) / sd * math.sqrt(365 if len(rets) > 400 else 252), 2)


def _cagr(equity_start: float, equity_end: float, days: int) -> float | None:
    if equity_start <= 0 or days < 30:
        return None
    years = days / 365.25
    if years <= 0 or equity_end <= 0:
        return None
    return round(((equity_end / equity_start) ** (1 / years) - 1) * 100.0, 2)


def _max_dd(curve: list[float]) -> float:
    peak = curve[0] if curve else 0
    dd = 0.0
    for x in curve:
        peak = max(peak, x)
        if peak > 0:
            dd = max(dd, (peak - x) / peak)
    return round(dd * 100.0, 2)


def run_symbol_backtest(
    df: pd.DataFrame,
    *,
    desk: str,
    book: str,
    symbol: str,
    signal_fn: Callable[..., TradeSignal],
    dxy: pd.Series | None = None,
    oos_frac: float = 0.33,
) -> BacktestReport:
    need = {"Open", "High", "Low", "Close"}
    if df is None or df.empty or not need.issubset(set(df.columns)):
        raise ValueError(f"OHLC incompleto para {symbol}")
    df = df.copy()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 120:
        raise ValueError(f"pocos datos {symbol}: {len(df)}")

    cost = COST_BPS[desk] / 10_000.0
    risk_pct = RISK_PCT[desk]
    target_pct = LEGACY_TARGET_PCT[desk] if book == "legacy" else None
    equity = START_EQUITY
    peak = equity
    cash = equity
    pos = None  # dict
    curve: list[float] = []
    daily_rets: list[float] = []
    closed: list[ClosedTrade] = []
    day_start = equity
    day_key = None
    blocked_today = False
    kill_at = None
    kill_dd = None
    kill_fired = False
    typical_stop = 0.04 if desk == "gold" else (0.035 if desk == "forex" else 0.08)
    trail_mult = 0.0

    def mark_to_market(px: float) -> float:
        if not pos:
            return cash
        return cash + pos["qty"] * px

    for i in range(1, len(df) - 1):
        row = df.iloc[i]
        nxt = df.iloc[i + 1]
        ts = df.index[i]
        date_s = str(getattr(ts, "date", lambda: ts)())
        if hasattr(ts, "date"):
            date_s = str(ts.date())
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        o_n = float(nxt.Open)

        if day_key != date_s:
            day_key = date_s
            day_start = equity
            blocked_today = False

        # Manage open position on this bar (stop / trail / target). Conservative: stop first.
        if pos:
            stop = float(pos["stop"])
            peak_px = max(float(pos["peak"]), h)
            pos["peak"] = peak_px
            trail_mult = float(pos.get("trail_atr_mult") or 0)
            atr_abs = float(pos.get("atr") or 0)
            risk_px = float(pos["entry"]) - float(pos["init_stop"])
            if risk_px > 0 and trail_mult > 0 and atr_abs > 0:
                if (peak_px - pos["entry"]) >= risk_px:  # +1R
                    trailed = peak_px - trail_mult * atr_abs
                    if trailed > stop:
                        stop = trailed
                        pos["stop"] = stop
            exit_px = None
            reason = None
            if l <= stop:
                exit_px = stop
                reason = "stop"
            elif target_pct and h >= pos["entry"] * (1 + target_pct):
                exit_px = pos["entry"] * (1 + target_pct)
                reason = "target"
            if exit_px is not None:
                gross = (exit_px / pos["entry"] - 1.0) * pos["qty"] * pos["entry"]
                fee = pos["notional"] * cost
                pnl = gross - fee
                cash += pos["notional"] + pnl
                r_mult = ((exit_px / pos["entry"] - 1.0) / pos["stop_pct"]) if pos["stop_pct"] else 0.0
                weekend = pos.get("held_weekend") is True
                closed.append(
                    ClosedTrade(
                        entry_date=pos["entry_date"],
                        exit_date=date_s,
                        side="long",
                        entry=pos["entry"],
                        exit=exit_px,
                        pnl_pct=round((exit_px / pos["entry"] - 1.0) * 100 - cost * 100, 3),
                        pnl_usd=round(pnl, 2),
                        r_multiple=round(r_mult, 2),
                        reason=reason or "",
                        stop_pct=round(pos["stop_pct"] * 100, 3),
                        weekend_hold=weekend,
                    )
                )
                pos = None

        equity = mark_to_market(c)
        curve.append(equity)
        if len(curve) >= 2 and curve[-2] > 0:
            daily_rets.append(curve[-1] / curve[-2] - 1.0)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak else 0
        if not kill_fired and dd >= MAX_DD_KILL_PCT:
            kill_fired = True
            kill_at = date_s
            kill_dd = round(dd, 2)

        if day_start > 0:
            day_pnl = (equity - day_start) / day_start * 100
            if day_pnl <= -MAX_DAILY_LOSS_PCT:
                blocked_today = True

        # Signals at close[i], fill next open — skip if in position or blocked
        if pos is not None or blocked_today or kill_fired:
            continue
        dxy_10d = None
        if dxy is not None and desk == "gold" and book == "new":
            try:
                aligned = dxy.reindex(df.index, method="ffill")
                if i >= 10 and pd.notna(aligned.iloc[i]) and pd.notna(aligned.iloc[i - 10]):
                    dxy_10d = float(aligned.iloc[i] / aligned.iloc[i - 10] - 1.0)
            except Exception:
                dxy_10d = None
        if desk == "gold" and book == "new":
            sig = signal_fn(df, i, dxy_10d=dxy_10d)
        else:
            sig = signal_fn(df, i)
        typical_stop = sig.stop_pct
        if sig.side != "buy":
            continue
        entry = o_n * (1 + cost / 2)  # slippage half-spread on entry
        stop_px = entry * (1 - sig.stop_pct)
        stop_dist = entry - stop_px
        if stop_dist <= 0:
            continue
        risk_usd = equity * (risk_pct / 100.0)
        qty = risk_usd / stop_dist
        notional = qty * entry
        # 1x cap
        notional = min(notional, cash, equity)
        if notional < 15:
            continue
        qty = notional / entry
        cash -= notional
        pos = {
            "qty": qty,
            "entry": entry,
            "notional": notional,
            "stop": stop_px,
            "init_stop": stop_px,
            "stop_pct": sig.stop_pct,
            "peak": entry,
            "atr": sig.atr_abs,
            "trail_atr_mult": sig.trail_atr_mult,
            "entry_date": str(df.index[i + 1].date()) if hasattr(df.index[i + 1], "date") else date_s,
            "held_weekend": False,
        }

        # Flag weekend hold for crypto (next bar weekday vs weekend)
        try:
            wd = int(df.index[i + 1].weekday())
            if desk == "crypto" and wd >= 5:
                pos["held_weekend"] = True
        except Exception:
            pass

    if pos:
        last = float(df["Close"].iloc[-1])
        cash += pos["qty"] * last
        pos = None
    equity = cash
    if curve:
        curve.append(equity)

    wins = [t for t in closed if t.pnl_usd > 0]
    losses = [t for t in closed if t.pnl_usd <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = abs(sum(t.pnl_usd for t in losses))
    pf = round(gp / gl, 3) if gl > 0 else (9.99 if gp > 0 else None)
    exp = round(sum(t.pnl_pct for t in closed) / len(closed), 3) if closed else None
    wr = round(len(wins) / len(closed) * 100.0, 2) if closed else None

    streak = worst = 0
    streak_usd = worst_usd = 0.0
    for t in closed:
        if t.pnl_usd <= 0:
            streak += 1
            streak_usd += t.pnl_usd
            worst = max(worst, streak)
            worst_usd = min(worst_usd, streak_usd)
        else:
            streak = 0
            streak_usd = 0.0

    n = len(df)
    oos_i = int(n * (1 - oos_frac))
    oos_start = str(df.index[oos_i].date()) if n > oos_i else None
    oos_trades = [t for t in closed if t.entry_date >= (oos_start or "9999")]
    # Approximate OOS equity path from full curve slice
    oos_curve = curve[oos_i:] if len(curve) > oos_i + 10 else curve
    oos_rets = []
    for a, b in zip(oos_curve, oos_curve[1:]):
        if a > 0:
            oos_rets.append(b / a - 1.0)
    oos_ret = None
    if oos_curve:
        oos_ret = round((oos_curve[-1] / oos_curve[0] - 1) * 100.0, 2) if oos_curve[0] else None

    days = max(1, (df.index[-1] - df.index[0]).days) if hasattr(df.index[-1] - df.index[0], "days") else len(df)
    crypto_note = {}
    if desk == "crypto":
        crypto_note = {
            "broker_stops": "GTC bracket stop en el broker paper, activo 24/7 incl. fin de semana",
            "daily_loss_weekend": True,
            "monitor": "autopilot 24/7 (interval minutes); si el host duerme, el stop GTC sigue en Alpaca paper",
            "unsupervised_window": "no se deja crypto sin stop de broker; no se requiere flatten pre-cierre US",
            "weekend_trades_held": sum(1 for t in closed if t.weekend_hold),
        }

    notes = [
        "Long only, apalancamiento 1.0, sin margen.",
        f"Entrada en open t+1 tras señal en close t (sin look-ahead).",
        f"Coste ida y vuelta {COST_BPS[desk]:.0f} bps (spread+slippage).",
        "Si high y low tocan stop y target el mismo día, se asume stop primero (conservador).",
    ]
    if kill_fired:
        notes.append(f"Kill-switch de mesa ({MAX_DD_KILL_PCT:.0f}% DD) se habría disparado el {kill_at} (DD {kill_dd}%).")
    else:
        notes.append(f"Kill-switch de mesa ({MAX_DD_KILL_PCT:.0f}% DD) NO se disparó en la muestra.")

    return BacktestReport(
        desk=desk,
        book=book,
        symbol=symbol,
        bars=len(df),
        start=str(df.index[0].date()),
        end=str(df.index[-1].date()),
        leverage=1.0,
        margin=False,
        stop_pct_typical=round(typical_stop * 100, 3),
        stop_r=1.0,
        trail_atr_mult=trail_mult if book == "new" else 0.0,
        risk_pct=risk_pct,
        cost_bps_roundtrip=COST_BPS[desk],
        trades=len(closed),
        wins=len(wins),
        losses=len(losses),
        win_rate=wr,
        expectancy_pct=exp,
        profit_factor=pf,
        cagr_pct=_cagr(START_EQUITY, equity, days),
        max_drawdown_pct=_max_dd(curve) if curve else None,
        sharpe=_sharpe(daily_rets),
        total_return_pct=round((equity / START_EQUITY - 1) * 100.0, 2),
        worst_losing_streak=worst,
        worst_losing_streak_usd=round(worst_usd, 2),
        kill_switch_fired=kill_fired,
        kill_switch_at=kill_at,
        kill_switch_dd_pct=kill_dd,
        oos_start=oos_start,
        oos_trades=len(oos_trades),
        oos_total_return_pct=oos_ret,
        oos_max_drawdown_pct=_max_dd(oos_curve) if oos_curve else None,
        oos_sharpe=_sharpe(oos_rets),
        oos_win_rate=round(sum(1 for t in oos_trades if t.pnl_usd > 0) / len(oos_trades) * 100, 2)
        if oos_trades
        else None,
        crypto_24_7=crypto_note,
        notes=notes,
        sample_trades=[asdict(t) for t in closed[:8]],
    )


def compare_desk(
    df: pd.DataFrame,
    *,
    desk: str,
    symbol: str,
    dxy: pd.Series | None = None,
) -> dict[str, Any]:
    fns = {
        "gold": (legacy_gold_signal, new_gold_signal),
        "forex": (legacy_forex_signal, new_forex_signal),
        "crypto": (legacy_crypto_signal, new_crypto_signal),
    }
    legacy_fn, new_fn = fns[desk]
    legacy = run_symbol_backtest(df, desk=desk, book="legacy", symbol=symbol, signal_fn=legacy_fn, dxy=dxy)
    new = run_symbol_backtest(df, desk=desk, book="new", symbol=symbol, signal_fn=new_fn, dxy=dxy)
    # Honest winner on OOS total return, then IS max DD as tie-break
    winner = "tie"
    if (new.oos_total_return_pct or -999) > (legacy.oos_total_return_pct or -999) + 0.5:
        winner = "new"
    elif (legacy.oos_total_return_pct or -999) > (new.oos_total_return_pct or -999) + 0.5:
        winner = "legacy"
    edge_note = (
        "La lógica nueva gana en OOS."
        if winner == "new"
        else (
            "La lógica actual gana en OOS — no se infla la nueva; se despliega igual por el marco de riesgo (1x, stop ATR, trail, kill)."
            if winner == "legacy"
            else "Sin ventaja clara en OOS."
        )
    )
    return {
        "desk": desk,
        "symbol": symbol,
        "winner_oos": winner,
        "edge_note": edge_note,
        "leverage": 1.0,
        "margin": False,
        "legacy": asdict(legacy),
        "new": asdict(new),
    }
