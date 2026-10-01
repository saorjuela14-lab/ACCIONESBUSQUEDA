"""Firm-book capital constants.

`FIRM_RETURN_BASE_USD` ($20) is the **trading book stamp** used by bootstrap,
reconcile, and Alpaca sync so sizing/risk keep a stable micro denominator.

Performance / P&L % (panel, briefings, Viernes, month report) MUST use the
real deposited base from Alpaca account activities via
`services.deposited_capital_service.get_deposited_base` — never this $20 figure
as a silent reporting fallback.
"""

from __future__ import annotations

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
