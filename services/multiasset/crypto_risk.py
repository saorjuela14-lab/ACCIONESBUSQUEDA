"""Crypto PAPER risk limits (Riesgo 2026-10-01). Never applied to LIVE equity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from utils.logging import get_logger

logger = get_logger(__name__)

BTC_ETH = frozenset({"BTC/USD", "ETH/USD"})
MAJORS = frozenset({
    "SOL/USD", "AVAX/USD", "LINK/USD", "DOT/USD", "ADA/USD", "LTC/USD",
    "BCH/USD", "UNI/USD", "ATOM/USD", "NEAR/USD", "XRP/USD", "DOGE/USD",
})
SPREAD_UNIVERSE_BPS = 30.0
SPREAD_BTC_BPS = 10.0
SPREAD_ETH_BPS = 10.0
SPREAD_MAJORS_BPS = 15.0
SPREAD_REJECT_MULT = 2.5

MAX_CRYPTO_EQUITY_PCT = 25.0
MAX_OPEN_RISK_PCT = 1.5  # aggregate, treated as one asset
MAX_POSITIONS = 6
BTC_ETH_EQUITY_PCT = 10.0
BTC_ETH_RISK_PCT = 0.5
ALT_EQUITY_PCT = 5.0
ALT_RISK_PCT = 0.25
ADV_PCT = 1.0  # 1% median daily Alpaca volume — BTC/ETH included
CORR_THRESHOLD = 0.7
GROUP_RISK_PCT = 1.0
GROUP_EQUITY_PCT = 15.0
RAMP_TRADES = 12
KILL_ALLOC_DD_PCT = 10.0
PAUSE_DAILY_PCT = 1.5
PAUSE_WEEKLY_PCT = 3.0
ACCUM_BRAKE_PCT = 5.0  # pause buys; 10% remains the hard kill
LOSS_STREAK_N = 3
LOSS_STREAK_PAUSE_HOURS = 24.0
MIN_NOTIONAL_USD = 50.0
DESK_ORDER_CAP_USD = 5_000.0
ALPACA_TAKER_BPS = 25.0
DEFAULT_SLIP_BPS = 0.0
VOL_TARGET = 0.25
MIN_QTY = {"BTC/USD": 0.0001, "ETH/USD": 0.001}


def _norm(symbol: str) -> str:
    s = (symbol or "").upper().replace(" ", "")
    if "/" not in s and s.endswith("USD") and len(s) > 3 and s.isalpha() is False:
        base = s[:-3]
        if base.isalpha():
            return f"{base}/USD"
    if "/" not in s and s.endswith("USD") and s[:-3].isalpha():
        return f"{s[:-3]}/USD"
    return s


def is_btc_eth(symbol: str) -> bool:
    return _norm(symbol) in BTC_ETH


def hard_spread_cap_bps(symbol: str) -> float:
    s = _norm(symbol)
    if s == "BTC/USD":
        return SPREAD_BTC_BPS
    if s == "ETH/USD":
        return SPREAD_ETH_BPS
    if s in MAJORS:
        return SPREAD_MAJORS_BPS
    return SPREAD_UNIVERSE_BPS


def spread_bps(bid: float | None, ask: float | None, last: float | None = None) -> float | None:
    try:
        b = float(bid or 0)
        a = float(ask or 0)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a <= 0 or a < b:
        return None
    mid = (a + b) / 2.0
    if last and float(last) > 0:
        mid = float(last)
    if mid <= 0:
        return None
    return (a - b) / mid * 10_000.0


def universe_spread_ok(symbol: str, median_bps: float | None) -> tuple[bool, str]:
    if median_bps is None:
        return False, "median_spread_unknown"
    cap = hard_spread_cap_bps(symbol)
    if float(median_bps) > cap:
        return False, f"median_spread {median_bps:.1f}bp > cap {cap:.1f}bp"
    return True, "ok"


def entry_spread_ok(symbol: str, *, live_bps: float | None, median_bps: float | None) -> tuple[bool, str]:
    """Fail closed: no moment spread → reject the entry."""
    ok, why = universe_spread_ok(symbol, median_bps)
    if not ok:
        return False, why
    if live_bps is None:
        return False, "live_spread_unknown"
    if float(live_bps) > SPREAD_REJECT_MULT * float(median_bps):
        return False, f"live_spread {live_bps:.1f}bp > {SPREAD_REJECT_MULT}× median {median_bps:.1f}"
    return True, "ok"


def per_name_caps(symbol: str, equity: float) -> tuple[float, float]:
    """(max_notional, max_risk_usd) for one name."""
    eq = max(0.0, float(equity or 0))
    if is_btc_eth(symbol):
        return eq * BTC_ETH_EQUITY_PCT / 100.0, eq * BTC_ETH_RISK_PCT / 100.0
    return eq * ALT_EQUITY_PCT / 100.0, eq * ALT_RISK_PCT / 100.0


def ramp_mult(n_trades: int, ramp_until: int = RAMP_TRADES, symbol: str | None = None) -> float:
    if symbol and is_btc_eth(symbol):
        return 1.0
    if int(n_trades or 0) >= int(ramp_until):
        return 1.0
    return 0.5


def alpaca_min_qty(symbol: str) -> float | None:
    return MIN_QTY.get(_norm(symbol))


def cost_fraction_bps(*, live_spread_bps: float | None, taker_bps: float = ALPACA_TAKER_BPS, slip_bps: float = DEFAULT_SLIP_BPS) -> float:
    return float(taker_bps) + float(live_spread_bps or 0) + float(slip_bps)


def cluster_symbols(corr: dict[tuple[str, str], float], symbols: Iterable[str], *, thresh: float = CORR_THRESHOLD) -> list[set[str]]:
    """Union-find: ρ≥thresh vs BTC or vs each other → one group."""
    names = [_norm(s) for s in symbols]
    parent = {s: s for s in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b), rho in (corr or {}).items():
        aa, bb = _norm(a), _norm(b)
        if aa not in parent or bb not in parent:
            continue
        try:
            r = float(rho)
        except (TypeError, ValueError):
            continue
        if r >= thresh:
            union(aa, bb)
    # Also attach anyone correlated with BTC into BTC's set even if BTC not in `symbols`.
    btc = "BTC/USD"
    if btc not in parent:
        parent[btc] = btc
        names.append(btc)
    groups: dict[str, set[str]] = {}
    for s in names:
        groups.setdefault(find(s), set()).add(s)
    return [g for g in groups.values() if len(g) >= 1]


@dataclass
class CryptoBook:
    equity: float
    crypto_notional: float
    open_risk_usd: float
    n_positions: int
    name_notional: dict[str, float]
    name_risk: dict[str, float]
    group_notional: dict[str, float]
    group_risk: dict[str, float]
    membership: dict[str, str]  # symbol -> group id


def group_id_for(symbol: str, groups: list[set[str]]) -> str:
    s = _norm(symbol)
    for i, g in enumerate(groups):
        if s in g:
            return f"g{i}"
    return f"solo:{s}"


def min_lot_fits(
    notional: float,
    entry: float,
    *,
    min_notional: float = MIN_NOTIONAL_USD,
    min_qty: float | None = None,
) -> bool:
    """True iff the sized ticket already covers the min lot. Never bump size up."""
    if float(notional) < float(min_notional) - 1e-9:
        return False
    if min_qty is not None and float(min_qty) > 0 and float(entry) > 0:
        if float(entry) * float(min_qty) > float(notional) + 1e-9:
            return False
    return True


def size_crypto_order(
    *,
    symbol: str,
    equity: float,
    entry: float,
    stop: float,
    book: CryptoBook,
    n_trades: int = 0,
    median_adv_usd: float | None = None,
    groups: list[set[str]] | None = None,
    max_positions: int | None = None,
    s_signal: float = 1.0,
    vol_30d: float | None = None,
    min_qty: float | None = None,
    live_spread_bps: float | None = None,
    desk_cap_usd: float | None = None,
) -> tuple[float, dict[str, Any]]:
    """Return notional (0 = reject) and diagnostics. Paper 1x.

    Size = min of: vol weight (S × min(1, 25%/vol) × name cap); 0.5%/0.25% risk vs
    8-ATR stop distance; 10%/5% equity; 1% median ADV (BTC/ETH included); correlated
    group caps; 25% sleeve; 1.5% aggregate risk; then × ramp. If min lot does not
    fit, skip — never enlarge.
    """
    info: dict[str, Any] = {"symbol": _norm(symbol)}
    if entry <= 0 or stop <= 0 or stop >= entry:
        info["reason"] = "invalid_stop"
        return 0.0, info
    cap_n = int(max_positions if max_positions is not None else MAX_POSITIONS)
    if book.n_positions >= cap_n and _norm(symbol) not in book.name_notional:
        info["reason"] = "max_positions"
        return 0.0, info
    s = max(0.0, min(1.0, float(s_signal)))
    if s <= 1e-12:
        info["reason"] = "S_zero"
        return 0.0, info
    if vol_30d is None or float(vol_30d) <= 0:
        info["reason"] = "vol_unknown"
        return 0.0, info

    cost_bps = cost_fraction_bps(live_spread_bps=live_spread_bps)
    risk_per_unit = (entry - stop) + entry * (cost_bps / 10_000.0)
    name_notional_cap, name_risk_cap = per_name_caps(symbol, equity)
    scale = min(1.0, VOL_TARGET / float(vol_30d))
    vol_notional = s * scale * name_notional_cap
    raw_risk = name_risk_cap / risk_per_unit * entry if risk_per_unit > 0 else 0.0

    sleeve_cap = equity * MAX_CRYPTO_EQUITY_PCT / 100.0
    sleeve_room = max(0.0, sleeve_cap - book.crypto_notional)
    name_room = max(0.0, name_notional_cap - book.name_notional.get(_norm(symbol), 0.0))
    agg_risk_cap = equity * MAX_OPEN_RISK_PCT / 100.0
    agg_risk_room = max(0.0, agg_risk_cap - book.open_risk_usd)
    notional_from_agg_risk = agg_risk_room / risk_per_unit * entry if risk_per_unit > 0 else 0.0
    desk_cap = float(desk_cap_usd if desk_cap_usd is not None else DESK_ORDER_CAP_USD)

    notional = min(vol_notional, raw_risk, sleeve_room, name_room, notional_from_agg_risk, desk_cap)

    if median_adv_usd and median_adv_usd > 0:
        adv_cap = float(median_adv_usd) * ADV_PCT / 100.0
        notional = min(notional, adv_cap)
        info["adv_cap"] = round(adv_cap, 2)

    gid = group_id_for(symbol, groups or [])
    g_not_cap = equity * GROUP_EQUITY_PCT / 100.0
    g_risk_cap = equity * GROUP_RISK_PCT / 100.0
    g_not_room = max(0.0, g_not_cap - book.group_notional.get(gid, 0.0))
    g_risk_room = max(0.0, g_risk_cap - book.group_risk.get(gid, 0.0))
    g_not_from_risk = g_risk_room / risk_per_unit * entry if risk_per_unit > 0 else 0.0
    notional = min(notional, g_not_room, g_not_from_risk)

    ramp = ramp_mult(n_trades, symbol=symbol)
    notional *= ramp
    info.update(
        {
            "vol_scale": round(scale, 6),
            "vol_notional": round(vol_notional, 2),
            "raw_risk": round(raw_risk, 2),
            "S": s,
            "ramp": ramp,
            "group": gid,
            "cost_bps": round(cost_bps, 4),
            "desk_cap": desk_cap,
            "stop_pct": round(risk_per_unit / entry * 100.0, 4),
        }
    )
    qty_floor = min_qty if min_qty is not None else alpaca_min_qty(symbol)
    if not min_lot_fits(notional, entry, min_qty=qty_floor):
        info["reason"] = "too_small"
        info["sized"] = round(notional, 4)
        return 0.0, info
    info["reason"] = "ok"
    return round(notional, 2), info


def kill_from_allocation_peak(
    *,
    peak_crypto_usd: float,
    crypto_usd: float,
    allocation_usd: float,
    kill_pct: float = KILL_ALLOC_DD_PCT,
) -> bool:
    """True when DD from peak ≥ 10% of the crypto allocation."""
    alloc = float(allocation_usd or 0)
    if alloc <= 0:
        return False
    dd = max(0.0, float(peak_crypto_usd or 0) - float(crypto_usd or 0))
    return dd >= alloc * (kill_pct / 100.0)


def daily_weekly_pause(
    *,
    day_pnl_pct: float,
    week_pnl_pct: float,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Rolling 24h / 7d PnL vs crypto allocation (not UTC calendar day)."""
    _ = now
    if float(day_pnl_pct) <= -PAUSE_DAILY_PCT:
        return True, f"rolling_24h_loss {day_pnl_pct:.2f}%"
    if float(week_pnl_pct) <= -PAUSE_WEEKLY_PCT:
        return True, f"rolling_7d_loss {week_pnl_pct:.2f}%"
    return False, None


def wealth_drawdown_pct(*, peak: float, wealth: float, allocation: float) -> float:
    alloc = float(allocation or 0)
    if alloc <= 0:
        return 0.0
    return max(0.0, float(peak or 0) - float(wealth or 0)) / alloc * 100.0


def accum_brake_triggered(dd_pct: float, *, brake_pct: float = ACCUM_BRAKE_PCT) -> bool:
    return float(dd_pct) >= float(brake_pct)


def loss_streak_pause(
    losses: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    n: int = LOSS_STREAK_N,
    hours: float = LOSS_STREAK_PAUSE_HOURS,
) -> tuple[bool, str | None]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    rows = list(losses or [])
    if len(rows) < int(n):
        return False, None
    tail = rows[-int(n) :]
    if not all(float(r.get("pnl_usd") or 0) < 0 for r in tail):
        return False, None
    last_at = tail[-1].get("at")
    try:
        last = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except Exception:
        return True, f"loss_streak_{n}"
    if (clock - last).total_seconds() <= float(hours) * 3600:
        return True, f"loss_streak_{n}_rolling_{hours:g}h"
    return False, None


def reset_allocation_kill(
    mark: dict[str, Any],
    *,
    actor: str,
    reason: str,
    current_wealth: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    out = dict(mark or {})
    out["kill_reset"] = {
        "actor": actor,
        "reason": reason,
        "at": clock.isoformat(),
        "prev_peak": out.get("peak_wealth_usd"),
        "prev_kill": out.get("kill_active"),
    }
    out["kill_active"] = False
    out["peak_wealth_usd"] = float(current_wealth or 0)
    return out
