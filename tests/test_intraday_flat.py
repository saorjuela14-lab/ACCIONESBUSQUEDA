"""Intraday / EOD smart flat helpers."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from utils.market_hours import US_EASTERN, in_eod_flat_window, minutes_to_regular_close


def test_eod_flat_window_around_close():
    dt = datetime(2026, 8, 5, 15, 30, tzinfo=US_EASTERN)
    assert minutes_to_regular_close(dt) == 30
    assert in_eod_flat_window(20, dt) is False

    dt2 = datetime(2026, 8, 5, 15, 40, tzinfo=US_EASTERN)
    assert minutes_to_regular_close(dt2) == 20
    assert in_eod_flat_window(20, dt2) is True

    dt3 = datetime(2026, 8, 5, 15, 55, tzinfo=US_EASTERN)
    assert in_eod_flat_window(20, dt3) is True

    dt4 = datetime(2026, 8, 5, 12, 0, tzinfo=US_EASTERN)
    assert in_eod_flat_window(20, dt4) is False


def test_smart_flat_classifies_green_vs_red():
    from services.intraday_flat_service import IntradayFlatService

    svc = IntradayFlatService.__new__(IntradayFlatService)
    settings = MagicMock()
    settings.intraday_flat_winners_only = True
    settings.intraday_flat_min_pnl_pct = 0.0
    settings.intraday_carry_max_loss_pct = 8.0
    svc._settings = settings

    green = SimpleNamespace(symbol="BBAI", avg_entry_price=3.0, current_price=3.05, unrealized_plpc=0.016)
    red = SimpleNamespace(symbol="SNAP", avg_entry_price=5.0, current_price=4.80, unrealized_plpc=-0.04)
    deep = SimpleNamespace(symbol="AMC", avg_entry_price=3.0, current_price=2.70, unrealized_plpc=-0.10)

    a1, r1 = IntradayFlatService._classify(svc, green, None)
    assert a1 == "close"
    assert "ganancia" in r1

    a2, r2 = IntradayFlatService._classify(svc, red, None)
    assert a2 == "carry"
    assert "rojo" in r2

    a3, r3 = IntradayFlatService._classify(svc, deep, None)
    assert a3 == "close"
    assert "perdida_max_carry" in r3

    invalidated = SimpleNamespace(thesis_invalidated=True, stop_loss=None)
    a4, _ = IntradayFlatService._classify(svc, red, invalidated)
    assert a4 == "close"
