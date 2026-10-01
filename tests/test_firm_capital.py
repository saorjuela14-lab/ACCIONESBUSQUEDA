"""Firm return baseline: deposited Alpaca base is the risk/sizing denominator."""

from domain.firm_capital import (
    FIRM_RETURN_BASE_USD,
    drawdown_pct_from_base,
    loss_pct_vs_base,
    pnl_usd_from_base,
    return_pct_from_base,
)


def test_legacy_stamp_constant_unchanged():
    """Price-band copy still mentions $20; it is not the trading denominator."""
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


def test_drawdown_and_kill_switch_vs_deposited_2176():
    base = 21.76
    # 5% kill-switch $ threshold = 0.05 * 21.76 = $1.088 → equity 20.672
    assert round(base * 0.05, 3) == 1.088
    assert drawdown_pct_from_base(20.50, base) == -5.79
    assert drawdown_pct_from_base(22.00, base) == 0.0
    assert drawdown_pct_from_base(21.76, base) == 0.0
    assert drawdown_pct_from_base(21.00, None) is None
    # total P&L vs deposits
    assert loss_pct_vs_base(20.50, base) == -5.79
    assert loss_pct_vs_base(21.00, base) == -3.49
    # session change vs deposited base (last_equity)
    assert loss_pct_vs_base(21.00, base, last_equity=21.50) == -2.30
