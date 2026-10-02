"""Firm-book capital helpers.

Trading / risk / sizing / DB `initial_capital` use `get_deposited_base()`
(canonical `DEPOSITED_BASE_USD`) — never a silent $20.

`FIRM_RETURN_BASE_USD` is a legacy micro-book stamp kept only so the unrelated
price-band copy ("presupuesto bajo $20", discovery under $20) stays stable.
"""

from __future__ import annotations

# Legacy micro-book stamp — NOT the risk/sizing denominator.
FIRM_RETURN_BASE_USD = 20.0


def return_pct_from_base(total_value: float | None, base: float = FIRM_RETURN_BASE_USD) -> float:
    """((total - base) / base) * 100, rounded to 2 decimals."""
    tv = float(total_value or 0.0)
    b = float(base or 0.0)
    if b <= 0:
        return 0.0
    return round(((tv - b) / b) * 100.0, 2)


def pnl_usd_from_base(total_value: float | None, base: float | None) -> float | None:
    """Equity minus deposited base. None when the base is unknown."""
    if total_value is None or base is None:
        return None
    b = float(base)
    if b <= 0:
        return None
    return round(float(total_value) - b, 2)


def drawdown_pct_from_base(total_value: float | None, base: float | None) -> float | None:
    """Underwater % vs deposited base (0 if flat/green). None if base unknown."""
    if total_value is None or base is None:
        return None
    b = float(base)
    if b <= 0:
        return None
    ret = ((float(total_value) - b) / b) * 100.0
    return round(min(0.0, ret), 2)


def loss_pct_vs_base(
    equity: float | None,
    base: float | None,
    *,
    last_equity: float | None = None,
) -> float | None:
    """P&L as % of deposited base. Prefers session change when last_equity is known."""
    if base is None:
        return None
    b = float(base)
    if b <= 0:
        return None
    eq = float(equity or 0.0)
    if last_equity is not None and float(last_equity) > 0:
        return round((eq - float(last_equity)) / b * 100.0, 2)
    return return_pct_from_base(eq, b)
