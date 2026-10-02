"""Daily live screen on top of the versioned Strategy A JSON gate.

Does not recompute the OOS backtest. Liquidity + spread are re-evaluated each day.
"""

from __future__ import annotations

from typing import Any

from services.multiasset.crypto_risk import entry_spread_ok, hard_spread_cap_bps, universe_spread_ok

MAJORS = frozenset({
    "SOL/USD", "AVAX/USD", "LINK/USD", "DOT/USD", "ADA/USD", "LTC/USD",
    "BCH/USD", "UNI/USD", "ATOM/USD", "NEAR/USD", "XRP/USD", "DOGE/USD",
})

# Documented Alpaca crypto taker/maker — used in the Estrategia file, not live-recomputed.
ALPACA_MAKER_BPS = 15.0
ALPACA_TAKER_BPS = 25.0


def evidence_gate(
    row: dict[str, Any],
    *,
    min_trades: int = 20,
) -> tuple[bool, str]:
    """Expectancy > 0 and enough OOS trades. Seed status does not skip evidence.

    The JSON universe is the list of names — not an evidence waiver.
    """
    if row.get("on_approved_universe") and row.get("status") not in {
        "seed",
        "seed_pending_strategy_backtest",
    }:
        return True, "approved_universe"
    try:
        exp = row.get("expectancy")
        n = row.get("n_trades") if row.get("n_trades") is not None else row.get("trades")
        if exp is None:
            return False, "expectancy_missing"
        if float(exp) <= 0:
            return False, f"expectancy {exp} <= 0"
        nn = int(n or 0)
        if nn < int(min_trades):
            return False, f"n_trades {nn} < {min_trades}"
    except (TypeError, ValueError):
        return False, "expectancy_invalid"
    return True, "oos_ok"


def liquidity_ok(adv_usd: float | None, *, min_adv_usd: float) -> tuple[bool, str]:
    floor = float(min_adv_usd or 0)
    if floor <= 0:
        return True, "adv_gate_off"
    if adv_usd is None:
        return False, "adv_unknown"
    if float(adv_usd) < floor:
        return False, f"adv_usd {adv_usd:.0f} < {floor:.0f}"
    return True, "ok"


def median_adv_usd_from_daily(df) -> float | None:
    if df is None or getattr(df, "empty", True):
        return None
    try:
        close = df["Close"].astype(float)
        vol = df["Volume"].astype(float)
        adv = (close * vol).tail(20)
        if adv.empty:
            return None
        v = float(adv.median())
        return v if v > 0 else None
    except Exception:
        return None


def screen_symbol(
    row: dict[str, Any],
    *,
    min_adv_usd: float,
    min_trades: int,
    live_spread_bps: float | None,
    adv_usd: float | None,
    tradable: bool = True,
) -> dict[str, Any]:
    """One-symbol daily screen. Never looks at LIVE equity."""
    sym = str(row.get("symbol") or "")
    report: dict[str, Any] = {
        "symbol": sym,
        "expectancy": row.get("expectancy"),
        "n_trades": row.get("n_trades") if row.get("n_trades") is not None else row.get("trades"),
        "median_spread_bps": row.get("median_spread_bps"),
        "status": row.get("status"),
        "spread_cap_bps": hard_spread_cap_bps(sym),
        "adv_usd": adv_usd,
        "live_spread_bps": live_spread_bps,
        "alpaca_maker_bps": ALPACA_MAKER_BPS,
        "alpaca_taker_bps": ALPACA_TAKER_BPS,
        "passed": False,
        "reasons": [],
    }
    if not tradable:
        report["reasons"].append("not_tradable_alpaca")
        return report
    ok_e, why_e = evidence_gate(row, min_trades=min_trades)
    if not ok_e:
        report["reasons"].append(why_e)
        return report
    report["evidence"] = why_e
    med = row.get("median_spread_bps")
    try:
        med_f = float(med) if med is not None else None
    except (TypeError, ValueError):
        med_f = None
    ok_u, why_u = universe_spread_ok(sym, med_f)
    if not ok_u:
        report["reasons"].append(why_u)
        return report
    ok_s, why_s = entry_spread_ok(sym, live_bps=live_spread_bps, median_bps=med_f)
    if not ok_s:
        report["reasons"].append(why_s)
        return report
    ok_l, why_l = liquidity_ok(adv_usd if adv_usd is not None else _row_adv(row), min_adv_usd=min_adv_usd)
    if not ok_l:
        report["reasons"].append(why_l)
        return report
    report["passed"] = True
    report["reasons"].append("pass")
    return report


def _row_adv(row: dict[str, Any]) -> float | None:
    v = row.get("median_adv_usd") or row.get("adv_usd")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_gate_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [r for r in rows if r.get("passed")]
    rejected = [r for r in rows if not r.get("passed")]
    return {
        "paper": True,
        "runtime_must_not_recompute_oos": True,
        "passed_count": len(passed),
        "rejected_count": len(rejected),
        "passed": passed,
        "rejected": rejected,
        "passed_symbols": [r["symbol"] for r in passed],
    }
