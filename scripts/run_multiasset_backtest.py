"""Download ≥2y OHLC and write honest legacy-vs-new backtest JSON (paper research)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.multiasset.backtest import compare_desk

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


def main() -> int:
    dxy = None
    try:
        dxy = _hist("DX-Y.NYB")["Close"]
    except Exception:
        dxy = None
    payload = {
        "as_of": pd.Timestamp.now("UTC").isoformat(),
        "leverage": 1.0,
        "margin": False,
        "disclaimer": (
            "Backtest diario, entrada open t+1, costes/slippage, OOS último tercio. "
            "No es garantía. Si new no gana en OOS se dice explícitamente."
        ),
        "desks": {},
    }
    for desk, (yf_sym, label) in UNIVERSE.items():
        df = _hist(yf_sym, 3)
        payload["desks"][desk] = compare_desk(
            df, desk=desk, symbol=label, dxy=dxy if desk == "gold" else None
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {OUT}")
    for desk, block in payload["desks"].items():
        print(desk, "winner_oos=", block["winner_oos"], block["edge_note"])
        for book in ("legacy", "new"):
            r = block[book]
            print(
                f"  {book}: n={r['trades']} wr={r['win_rate']} exp={r['expectancy_pct']} "
                f"PF={r['profit_factor']} CAGR={r['cagr_pct']} DD={r['max_drawdown_pct']} "
                f"Sharpe={r['sharpe']} OOS_ret={r['oos_total_return_pct']} "
                f"streak={r['worst_losing_streak']} kill={r['kill_switch_fired']}@{r['kill_switch_at']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
