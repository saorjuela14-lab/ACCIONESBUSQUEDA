"""Download ≥2y OHLC and write honest legacy-vs-new backtest JSON (paper research)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.multiasset.backtest import (
    GOLD_ROBUSTNESS_WINDOWS,
    compare_desk,
    run_symbol_backtest,
)
from services.multiasset.signals import new_gold_signal

OUT = Path("services/multiasset/backtest_results.json")
UNIVERSE = {
    "gold": ("GLD", "GLD"),
    "forex": ("UUP", "UUP"),
    "crypto": ("BTC-USD", "BTC/USD"),
}


def _hist(symbol: str, years: int = 3) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{years}y", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"sin datos {symbol}")
    return df


def _hist_range(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"sin datos {symbol} {start} {end}")
    return df


def _print_book(label: str, r: dict) -> None:
    print(
        f"  {label}: n={r['trades']} wr={r['win_rate']} exp={r['expectancy_pct']} "
        f"CI95=[{r.get('expectancy_ci95_low')}, {r.get('expectancy_ci95_high')}] "
        f"PF={r['profit_factor']} ret={r['total_return_pct']} DD={r['max_drawdown_pct']} "
        f"OOS_n={r['oos_trades']} OOS_ret={r['oos_total_return_pct']} "
        f"OOS_exp={r.get('oos_expectancy_pct')} "
        f"OOS_CI95=[{r.get('oos_expectancy_ci95_low')}, {r.get('oos_expectancy_ci95_high')}] "
        f"B&H_OOS={r.get('buy_hold_oos_return_pct')} "
        f"kill={r['kill_switch_fired']}@{r['kill_switch_at']}"
    )


def main() -> int:
    dxy = None
    try:
        dxy = _hist("DX-Y.NYB")["Close"]
    except Exception:
        dxy = None
    payload: dict = {
        "as_of": pd.Timestamp.now("UTC").isoformat(),
        "leverage": 1.0,
        "margin": False,
        "disclaimer": (
            "Backtest diario, entrada open t+1, costes/slippage, OOS último tercio. "
            "Stop fill = min(stop, open); trail peak AFTER stop eval. "
            "El bloque `desks` es el parche de 2 líneas (sin salida por sell ni tope semanal 12%). "
            "`with_sell_and_weekly` modela ambas. No es garantía."
        ),
        "fill_fix": {
            "stop": "if low<=stop: exit=min(stop, open)",
            "trail": "peak updated after stop evaluation of that bar",
        },
        "desks": {},
        "with_sell_and_weekly": {},
        "gold_robustness": {},
    }
    data = {}
    for desk, (yf_sym, label) in UNIVERSE.items():
        df = _hist(yf_sym, 3)
        data[desk] = df
        payload["desks"][desk] = compare_desk(
            df,
            desk=desk,
            symbol=label,
            dxy=dxy if desk == "gold" else None,
            use_sell_exits=False,
            apply_weekly_loss=False,
        )
        payload["with_sell_and_weekly"][desk] = compare_desk(
            df,
            desk=desk,
            symbol=label,
            dxy=dxy if desk == "gold" else None,
            use_sell_exits=True,
            apply_weekly_loss=True,
        )

    try:
        dxy_long = _hist_range("DX-Y.NYB", "2011-06-01", "2019-06-02")["Close"]
        gld_long = _hist_range("GLD", "2011-06-01", "2019-06-02")
        for name, start, end in GOLD_ROBUSTNESS_WINDOWS:
            df = gld_long.loc[start:end]
            payload["gold_robustness"][name] = {
                "window": [start, end],
                "fill_fix_only": compare_desk(
                    df,
                    desk="gold",
                    symbol="GLD",
                    dxy=dxy_long,
                    use_sell_exits=False,
                    apply_weekly_loss=False,
                ),
                "with_sell_and_weekly": compare_desk(
                    df,
                    desk="gold",
                    symbol="GLD",
                    dxy=dxy_long,
                    use_sell_exits=True,
                    apply_weekly_loss=True,
                ),
            }
            r = run_symbol_backtest(
                df,
                desk="gold",
                book="new",
                symbol="GLD",
                signal_fn=new_gold_signal,
                dxy=dxy_long,
                use_sell_exits=False,
                apply_weekly_loss=False,
            )
            print(
                f"GOLD {name} fill-fix new: n={r.trades} PF={r.profit_factor} "
                f"ret={r.total_return_pct} DD={r.max_drawdown_pct} kill={r.kill_switch_at}"
            )
    except Exception as exc:
        payload["gold_robustness"]["error"] = str(exc)
        print("gold robustness failed", exc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {OUT}")
    print("=== fill-fix only (2 líneas, sin sell/weekly) ===")
    for desk, block in payload["desks"].items():
        print(desk, "winner_oos=", block["winner_oos"], "B&H_OOS", block["buy_hold_oos"]["return_pct"])
        for book in ("legacy", "new"):
            _print_book(book, block[book])
    print("=== with sell exits + weekly 12% ===")
    for desk, block in payload["with_sell_and_weekly"].items():
        print(desk, "winner_oos=", block["winner_oos"])
        _print_book("new", block["new"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
