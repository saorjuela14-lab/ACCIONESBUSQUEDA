"""Crypto PAPER risk limits (Riesgo 2026-10-01). Never applied to LIVE equity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from utils.logging import get_logger

logger = get_logger(__name__)

BTC_ETH = frozenset({"BTC/USD", "ETH/USD"})
SOL = "SOL/USD"

MAX_CRYPTO_EQUITY_PCT = 25.0
MAX_OPEN_RISK_PCT = 1.5  # aggregate, treated as one asset
MAX_POSITIONS = 6
BTC_ETH_EQUITY_PCT = 10.0
BTC_ETH_RISK_PCT = 0.5
ALT_EQUITY_PCT = 5.0
ALT_RISK_PCT = 0.25
ALT_ADV_PCT = 1.0
CORR_THRESHOLD = 0.7
GROUP_RISK_PCT = 1.0
GROUP_EQUITY_PCT = 15.0
SPREAD_UNIVERSE_BPS = 30.0
SPREAD_BTC_BPS = 10.0
SPREAD_ETH_BPS = 10.0
SPREAD_SOL_BPS = 15.0
SPREAD_REJECT_MULT = 2.5
RAMP_TRADES = 12
KILL_ALLOC_DD_PCT = 10.0
PAUSE_DAILY_PCT = 1.5
PAUSE_WEEKLY_PCT = 3.0


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
    if s == SOL:
        return SPREAD_SOL_BPS
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
    ok, why = universe_spread_ok(symbol, median_bps)
    if not ok:
        return False, why
    if live_bps is None:
        return True, "live_spread_unknown_use_median"
    if float(live_bps) > SPREAD_REJECT_MULT * float(median_bps):
        return False, f"live_spread {live_bps:.1f}bp > {SPREAD_REJECT_MULT}× median {median_bps:.1f}"
    return True, "ok"


def per_name_caps(symbol: str, equity: float) -> tuple[float, float]:
    """(max_notional, max_risk_usd) for one name."""
    eq = max(0.0, float(equity or 0))
    if is_btc_eth(symbol):
        return eq * BTC_ETH_EQUITY_PCT / 100.0, eq * BTC_ETH_RISK_PCT / 100.0
    return eq * ALT_EQUITY_PCT / 100.0, eq * ALT_RISK_PCT / 100.0


def ramp_mult(n_trades: int, ramp_until: int = RAMP_TRADES) -> float:
    if int(n_trades or 0) >= int(ramp_until):
        return 1.0
    return 0.5


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
) -> tuple[float, dict[str, Any]]:
    """Return notional (0 = reject) and diagnostics. Paper 1x."""
    info: dict[str, Any] = {"symbol": _norm(symbol)}
    if entry <= 0 or stop <= 0 or stop >= entry:
        info["reason"] = "invalid_stop"
        return 0.0, info
    if book.n_positions >= MAX_POSITIONS and _norm(symbol) not in book.name_notional:
        info["reason"] = "max_positions"
        return 0.0, info

    risk_per_unit = entry - stop
    name_notional_cap, name_risk_cap = per_name_caps(symbol, equity)
    risk_budget = name_risk_cap * ramp_mult(n_trades)
    raw = risk_budget / risk_per_unit * entry if risk_per_unit > 0 else 0.0

    sleeve_cap = equity * MAX_CRYPTO_EQUITY_PCT / 100.0
    sleeve_room = max(0.0, sleeve_cap - book.crypto_notional)
    name_room = max(0.0, name_notional_cap - book.name_notional.get(_norm(symbol), 0.0))
    agg_risk_cap = equity * MAX_OPEN_RISK_PCT / 100.0
    agg_risk_room = max(0.0, agg_risk_cap - book.open_risk_usd)
    notional_from_agg_risk = agg_risk_room / risk_per_unit * entry if risk_per_unit > 0 else 0.0

    notional = min(raw, sleeve_room, name_room, notional_from_agg_risk)

    if not is_btc_eth(symbol) and median_adv_usd and median_adv_usd > 0:
        adv_cap = median_adv_usd * ALT_ADV_PCT / 100.0
        notional = min(notional, adv_cap)
        info["adv_cap"] = round(adv_cap, 2)

    gid = group_id_for(symbol, groups or [])
    g_not_cap = equity * GROUP_EQUITY_PCT / 100.0
    g_risk_cap = equity * GROUP_RISK_PCT / 100.0
    g_not_room = max(0.0, g_not_cap - book.group_notional.get(gid, 0.0))
    g_risk_room = max(0.0, g_risk_cap - book.group_risk.get(gid, 0.0))
    g_not_from_risk = g_risk_room / risk_per_unit * entry if risk_per_unit > 0 else 0.0
    notional = min(notional, g_not_room, g_not_from_risk)

    notional *= ramp_mult(n_trades)
    if notional < 10:
        info["reason"] = "too_small"
        return 0.0, info
    info.update(
        {
            "reason": "ok",
            "raw": round(raw, 2),
            "ramp": ramp_mult(n_trades),
            "group": gid,
            "stop_pct": round(risk_per_unit / entry * 100.0, 4),
        }
    )
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
    _ = now
    if float(day_pnl_pct) <= -PAUSE_DAILY_PCT:
        return True, f"daily_loss {day_pnl_pct:.2f}%"
    if float(week_pnl_pct) <= -PAUSE_WEEKLY_PCT:
        return True, f"weekly_loss {week_pnl_pct:.2f}% pause_until_monday"
    return False, None
