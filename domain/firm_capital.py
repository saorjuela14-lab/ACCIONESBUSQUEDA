"""Shared firm-book capital baseline for return calculations.

Alpaca equity (~$21–22) must not become the return denominator — the desk
treats $20 as the starting base for performance.
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
