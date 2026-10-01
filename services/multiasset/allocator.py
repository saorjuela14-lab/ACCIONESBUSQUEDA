"""Desk director: allocate the paper sleeve by regime + recent expectancy/PF.

Cuts capital automatically when a specialist's profit factor < 1 or expectancy < 0.
Never touches the LIVE equity book.
"""

from __future__ import annotations

from typing import Any

from domain.multiasset import AssetDeskId, MultiAssetTrackRecord
from services.multiasset.risk_engine import MultiAssetRiskPolicy, policy_from_settings
from utils.market_hours import is_market_open

BASE_RTH: dict[AssetDeskId, float] = {"gold": 0.40, "forex": 0.25, "crypto": 0.35}
BASE_OFF: dict[AssetDeskId, float] = {"gold": 0.0, "forex": 0.0, "crypto": 1.0}


def _pf(tr: MultiAssetTrackRecord | None) -> float | None:
    if tr is None or tr.trades_closed < 4:
        return None
    wins = [t for t in tr.recent_closed if (t.pnl_usd or 0) > 0]
    losses = [t for t in tr.recent_closed if (t.pnl_usd or 0) < 0]
    gp = sum(float(t.pnl_usd or 0) for t in wins)
    gl = abs(sum(float(t.pnl_usd or 0) for t in losses))
    if gl <= 0:
        return 9.9 if gp > 0 else None
    return round(gp / gl, 3)


def _expectancy(tr: MultiAssetTrackRecord | None) -> float | None:
    if tr is None or tr.trades_closed < 4:
        return None
    pnls = [float(t.pnl_pct or 0) for t in tr.recent_closed if t.pnl_pct is not None]
    if len(pnls) < 4:
        return tr.trades_avg_pnl_pct
    return round(sum(pnls) / len(pnls), 3)


def allocate(
    *,
    market_open: bool | None = None,
    records: dict[AssetDeskId, MultiAssetTrackRecord] | None = None,
    policy: MultiAssetRiskPolicy | None = None,
) -> dict[str, Any]:
    """Return desk weights that sum to 1. Underperformers get haircut, not zero unless PF<<1."""
    policy = policy or policy_from_settings()
    rth = is_market_open() if market_open is None else market_open
    base = dict(BASE_RTH if rth else BASE_OFF)
    notes: list[str] = []
    scores: dict[str, float] = {}
    recs = records or {}
    for desk, w0 in base.items():
        if w0 <= 0:
            scores[desk] = 0.0
            continue
        tr = recs.get(desk)
        pf = _pf(tr)
        exp = _expectancy(tr)
        mult = 1.0
        if pf is not None and pf < 0.8:
            mult *= 0.35
            notes.append(f"{desk}: PF {pf:.2f}<0.8 → capital ×0.35")
        elif pf is not None and pf < 1.0:
            mult *= 0.6
            notes.append(f"{desk}: PF {pf:.2f}<1 → capital ×0.6")
        elif pf is not None and pf >= 1.4:
            mult *= 1.15
            notes.append(f"{desk}: PF {pf:.2f} fuerte → capital ×1.15")
        if exp is not None and exp < 0:
            mult *= 0.5
            notes.append(f"{desk}: expectancy {exp:.2f}% <0 → ×0.5")
        scores[desk] = max(0.0, w0 * mult)

    total = sum(scores.values())
    if total <= 0:
        weights = dict(base)
        notes.append("sin muestra suficiente — pesos base")
    else:
        weights = {k: round(v / total, 4) for k, v in scores.items()}

    return {
        "weights": weights,
        "market_open": rth,
        "mode": "rth_director" if rth else "offhours_crypto_24_7",
        "notes": notes[:8],
        "profit_factors": {d: _pf(recs.get(d)) for d in base},
        "expectancy_pct": {d: _expectancy(recs.get(d)) for d in base},
        "policy_risk_pct": policy.risk_pct,
        "leverage": 1.0,
        "paper": True,
    }
