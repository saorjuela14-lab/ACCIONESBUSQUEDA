"""Firm return baseline: $20 stays the trading stamp; P&L uses an explicit base."""

from domain.firm_capital import FIRM_RETURN_BASE_USD, pnl_usd_from_base, return_pct_from_base


def test_trading_stamp_is_twenty():
    """Sizing/bootstrap still stamp $20 — do not use this as silent P&L fallback."""
    assert FIRM_RETURN_BASE_USD == 20.0


def test_return_from_base_positive():
    assert return_pct_from_base(21.68) == 8.4


def test_return_from_base_flat():
    assert return_pct_from_base(20.0) == 0.0


def test_return_from_base_down():
    assert return_pct_from_base(18.0) == -10.0


def test_return_vs_deposited_underwater():
    # equity 20.99 vs $21.74 deposited → ~−3.45%
    assert return_pct_from_base(20.99, 21.74) == -3.45


def test_pnl_usd_from_base():
    assert pnl_usd_from_base(20.99, 21.74) == -0.75
    assert pnl_usd_from_base(22.0, 21.74) == 0.26
    assert pnl_usd_from_base(21.0, None) is None
    assert pnl_usd_from_base(21.0, 0) is None
