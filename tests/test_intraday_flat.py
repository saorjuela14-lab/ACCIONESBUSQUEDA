"""Intraday-only EOD flat window helpers."""

from datetime import datetime

from utils.market_hours import US_EASTERN, in_eod_flat_window, minutes_to_regular_close


def test_eod_flat_window_around_close():
    # 15:30 ET — outside 20m window
    dt = datetime(2026, 8, 5, 15, 30, tzinfo=US_EASTERN)
    assert minutes_to_regular_close(dt) == 30
    assert in_eod_flat_window(20, dt) is False

    # 15:40 ET — inside 20m window
    dt2 = datetime(2026, 8, 5, 15, 40, tzinfo=US_EASTERN)
    assert minutes_to_regular_close(dt2) == 20
    assert in_eod_flat_window(20, dt2) is True

    # 15:55 ET — still inside
    dt3 = datetime(2026, 8, 5, 15, 55, tzinfo=US_EASTERN)
    assert in_eod_flat_window(20, dt3) is True

    # Midday — not flat
    dt4 = datetime(2026, 8, 5, 12, 0, tzinfo=US_EASTERN)
    assert in_eod_flat_window(20, dt4) is False
