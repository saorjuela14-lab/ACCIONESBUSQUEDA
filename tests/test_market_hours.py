"""Tests for market hours utilities."""

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_hours import is_market_open, is_trading_day, should_run_automation

ET = ZoneInfo("America/New_York")


def test_weekend_not_trading_day():
    saturday = datetime(2026, 6, 27, 12, 0, tzinfo=ET)
    assert is_trading_day(saturday) is False


def test_weekday_trading_day():
    monday = datetime(2026, 6, 29, 12, 0, tzinfo=ET)
    assert is_trading_day(monday) is True


def test_market_open_during_hours():
    midday = datetime(2026, 6, 29, 12, 0, tzinfo=ET)
    assert is_market_open(midday) is True


def test_market_closed_after_hours():
    post_market = datetime(2026, 6, 29, 17, 0, tzinfo=ET)
    assert is_market_open(post_market) is False
    assert should_run_automation(post_market) is True  # post-market window
