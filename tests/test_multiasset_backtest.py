"""Backtest + risk unit tests for the paper multi-asset desk (no LIVE)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.multiasset.backtest import compare_desk, run_symbol_backtest
from services.multiasset.risk_engine import size_notional_1x, trail_stop
from services.multiasset.signals import (
    TradeSignal,
    legacy_gold_signal,
    new_crypto_signal,
    new_gold_signal,
)


def _trending_ohlc(n: int = 400, start: float = 100.0, drift: float = 0.0015, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = [start]
    for _ in range(n - 1):
        close.append(close[-1] * (1 + drift + float(rng.normal(0, 0.008))))
    close = np.array(close)
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    open_ = np.r_[close[0], close[:-1]]
    vol = rng.uniform(1e6, 2e6, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


def test_size_is_1x_never_above_equity():
    n, info = size_notional_1x(
        equity=1000,
        cash=1000,
        desk_budget=1000,
        entry=100,
        stop=97,  # 3%
        risk_pct=3.0,
        open_notional=0,
        max_leverage=1.0,
    )
    # risk $30 / $3 dist = 10 shares * 100 = $1000, capped at equity 1x
    assert n <= 1000
    assert info["leverage_cap"] == 1.0
    assert info["stop_r"] == 1.0
    over, _ = size_notional_1x(
        equity=100,
        cash=100,
        desk_budget=10_000,
        entry=50,
        stop=40,
        risk_pct=3.0,
        max_leverage=1.0,
    )
    assert over <= 100


def test_trail_only_ratchets_up_after_1r():
    stop, moved = trail_stop(entry=100, stop=96, peak=100, price=101, trail_atr_abs=3, arm_r=1)
    assert moved is False
    stop2, moved2 = trail_stop(entry=100, stop=96, peak=108, price=108, trail_atr_abs=3, arm_r=1)
    assert moved2 is True
    assert stop2 > 96


def test_new_gold_signal_no_lookahead_on_donchian():
    df = _trending_ohlc()
    sig = new_gold_signal(df, 200)
    assert sig.stop_r == 1.0
    assert sig.side in {"buy", "hold", "sell"}


def test_backtest_legacy_vs_new_has_required_fields():
    df = _trending_ohlc(500, drift=0.002)
    out = compare_desk(df, desk="gold", symbol="GLD")
    assert out["leverage"] == 1.0
    assert out["margin"] is False
    for book in ("legacy", "new"):
        r = out[book]
        assert r["stop_r"] == 1.0
        assert r["leverage"] == 1.0
        assert r["margin"] is False
        assert "worst_losing_streak" in r
        assert "kill_switch_fired" in r
        assert r["cost_bps_roundtrip"] > 0
        assert r["oos_start"]
        assert "buy_hold_oos_return_pct" in r
        assert "oos_expectancy_pct" in r
    # kill-switch metadata present even if it did not fire
    assert out["new"]["kill_switch_fired"] in (True, False)


def test_crypto_backtest_weekend_note():
    idx = pd.date_range("2023-01-01", periods=400, freq="D")
    rng = np.random.default_rng(2)
    close = 20000 * np.cumprod(1 + 0.001 + rng.normal(0, 0.02, 400))
    df = pd.DataFrame(
        {
            "Open": np.r_[close[0], close[:-1]],
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1e6,
        },
        index=idx,
    )
    r = run_symbol_backtest(df, desk="crypto", book="new", symbol="BTC-USD", signal_fn=new_crypto_signal)
    assert r.crypto_24_7.get("daily_loss_weekend") is True
    assert "GTC" in r.crypto_24_7.get("broker_stops", "")
    assert r.leverage == 1.0


def test_legacy_gold_warmup_hold():
    df = _trending_ohlc(30)
    sig = legacy_gold_signal(df, 5)
    assert sig.side == "hold"


def _forced_buy(df, i, **kwargs):
    return TradeSignal(
        side="buy",
        stop_pct=0.10,
        stop_r=1.0,
        trail_atr_mult=2.0,
        atr_abs=5.0,
        reason="forced",
    )


def _flat_then(n: int, patches: dict[int, tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=n)
    rows = []
    for i in range(n):
        o, h, l, c = patches.get(i, (100.0, 100.2, 99.8, 100.0))
        rows.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6})
    return pd.DataFrame(rows, index=idx)


def test_stop_fills_at_min_stop_open_on_gap():
    """Gap through stop fills at open, not at the stop price."""
    df = _flat_then(130, {20: (85.0, 86.0, 80.0, 82.0)})
    r = run_symbol_backtest(
        df, desk="gold", book="new", symbol="GLD", signal_fn=_forced_buy
    )
    gap = [t for t in r.sample_trades if t["reason"] == "stop_gap"]
    assert gap, r.sample_trades[:3]
    assert gap[0]["exit"] == 85.0
    assert gap[0]["exit"] < gap[0]["entry"] * 0.95


def test_trail_peak_updated_after_stop_eval():
    """Same-bar high must not raise the trail before the stop is checked."""
    # Bar 10: closed high 112 arms +1R for the *next* bar.
    # Bar 11: high 125 / low 100.5 — lagged trail ~102, lookahead would fill ~115.
    df = _flat_then(
        130,
        {
            10: (100.0, 112.0, 99.5, 110.0),
            11: (110.0, 125.0, 100.5, 108.0),
        },
    )
    r = run_symbol_backtest(
        df, desk="gold", book="new", symbol="GLD", signal_fn=_forced_buy
    )
    stopped = [t for t in r.sample_trades if t["reason"] in {"stop", "stop_gap"}]
    assert stopped
    # Must not fill at the same-bar trailed price (~115). Honest fill is the prior trail.
    assert stopped[0]["exit"] < 110.0


def test_long_stop_fill_helper():
    from services.multiasset.backtest import long_stop_fill

    px, reason = long_stop_fill(85.0, 80.0, 90.0)
    assert px == 85.0 and reason == "stop_gap"
    px, reason = long_stop_fill(100.0, 89.0, 90.0)
    assert px == 90.0 and reason == "stop"
    px, reason = long_stop_fill(100.0, 91.0, 90.0)
    assert px is None


def test_expectancy_ci_and_buy_hold_fields():
    df = _trending_ohlc(400, drift=0.002)
    out = compare_desk(df, desk="gold", symbol="GLD")
    assert "buy_hold_oos" in out
    r = out["new"]
    assert "oos_trades" in r
    assert "expectancy_ci95_low" in r
    assert r["buy_hold_oos_return_pct"] is not None
    from services.multiasset.backtest import expectancy_mean_ci95

    mean, lo, hi = expectancy_mean_ci95([1.0, 2.0, 3.0, 4.0])
    assert mean == 2.5
    assert lo is not None and hi is not None and lo < mean < hi
