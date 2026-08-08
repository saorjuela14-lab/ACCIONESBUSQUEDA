"""Firm return baseline is $20, not live Alpaca equity."""

from domain.firm_capital import FIRM_RETURN_BASE_USD, return_pct_from_base


def test_base_is_twenty():
    assert FIRM_RETURN_BASE_USD == 20.0


def test_return_from_base_positive():
    # e.g. equity 21.68 vs $20 start → +8.4%
    assert return_pct_from_base(21.68) == 8.4


def test_return_from_base_flat():
    assert return_pct_from_base(20.0) == 0.0


def test_return_from_base_down():
    assert return_pct_from_base(18.0) == -10.0
