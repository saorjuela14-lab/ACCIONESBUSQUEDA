"""Interval and period mapping across market data providers."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

# yfinance interval -> (polygon multiplier, polygon timespan) | alpha_vantage interval
INTERVAL_MAP: dict[str, dict[str, str | tuple[int, str]]] = {
    "1m": {"polygon": (1, "minute"), "alpha_vantage": "1min", "alpaca": "1Min"},
    "2m": {"polygon": (2, "minute"), "alpha_vantage": "1min", "alpaca": "1Min"},
    "5m": {"polygon": (5, "minute"), "alpha_vantage": "5min", "alpaca": "5Min"},
    "15m": {"polygon": (15, "minute"), "alpha_vantage": "15min", "alpaca": "15Min"},
    "30m": {"polygon": (30, "minute"), "alpha_vantage": "30min", "alpaca": "30Min"},
    "60m": {"polygon": (1, "hour"), "alpha_vantage": "60min", "alpaca": "1Hour"},
    "1h": {"polygon": (1, "hour"), "alpha_vantage": "60min", "alpaca": "1Hour"},
    "4h": {"polygon": (4, "hour"), "alpha_vantage": "60min", "alpaca": "1Hour"},
    "1d": {"polygon": (1, "day"), "alpha_vantage": "daily", "alpaca": "1Day"},
    "1wk": {"polygon": (1, "week"), "alpha_vantage": "weekly", "alpaca": "1Week"},
    "1mo": {"polygon": (1, "month"), "alpha_vantage": "monthly", "alpaca": "1Month"},
}

PERIOD_DAYS: dict[str, int] = {
    "1d": 1,
    "5d": 5,
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
    "ytd": 365,
    "max": 3650,
}


def period_to_date_range(period: str) -> tuple[str, str]:
    """Convert yfinance-style period to (from_date, to_date) ISO strings."""
    days = PERIOD_DAYS.get(period, 365)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def normalize_interval(interval: str) -> str:
    """Normalize interval aliases."""
    aliases = {"1H": "1h", "4H": "4h", "1D": "1d", "1W": "1wk", "1M": "1mo"}
    return aliases.get(interval, interval)


# Max calendar days since last bar before history is considered stale for provider fallback.
_STALE_MAX_AGE_DAYS: dict[str, int] = {
    "1m": 2,
    "2m": 2,
    "5m": 2,
    "15m": 2,
    "30m": 2,
    "60m": 3,
    "1h": 3,
    "4h": 5,
    "1d": 5,
    "1wk": 16,
    "1mo": 45,
}

# Beyond this age we treat the series as delisted / no longer trading.
_DELISTED_MIN_AGE_DAYS: dict[str, int] = {
    "1m": 14,
    "2m": 14,
    "5m": 14,
    "15m": 14,
    "30m": 14,
    "60m": 21,
    "1h": 21,
    "4h": 30,
    "1d": 60,
    "1wk": 90,
    "1mo": 180,
}

PERIOD_RANK: dict[str, int] = {
    "1d": 0,
    "5d": 1,
    "1mo": 2,
    "3mo": 3,
    "6mo": 4,
    "1y": 5,
    "2y": 6,
    "5y": 7,
    "10y": 8,
    "ytd": 5,
    "max": 9,
}


def longer_period(a: str, b: str) -> str:
    """Return the longer of two yfinance-style periods."""
    return a if PERIOD_RANK.get(a, 0) >= PERIOD_RANK.get(b, 0) else b


def last_bar_timestamp(df: Any) -> pd.Timestamp | None:
    """Naive UTC-normalized timestamp of the last bar, or None."""
    if df is None or getattr(df, "empty", True):
        return None
    try:
        ts = pd.Timestamp(df.index[-1])
    except Exception:
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def history_age_days(df: Any, *, now: datetime | None = None) -> int | None:
    """Calendar days between last bar and now (UTC)."""
    last = last_bar_timestamp(df)
    if last is None:
        return None
    ref = now or datetime.now(timezone.utc)
    ref_ts = pd.Timestamp(ref)
    if ref_ts.tzinfo is not None:
        ref_ts = ref_ts.tz_convert("UTC").tz_localize(None)
    return max(0, int((ref_ts.normalize() - last).days))


def is_history_stale(df: Any, interval: str, *, now: datetime | None = None) -> bool:
    """True when last bar is older than the live threshold for this interval."""
    age = history_age_days(df, now=now)
    if age is None:
        return True
    key = normalize_interval(interval)
    return age > _STALE_MAX_AGE_DAYS.get(key, 5)


def assess_market_status(
    df: Any,
    interval: str,
    *,
    now: datetime | None = None,
) -> tuple[str, int | None, str | None]:
    """Return (market_status, stale_days, as_of ISO date).

    Statuses: live | stale | delisted | unavailable
    """
    last = last_bar_timestamp(df)
    if last is None:
        return "unavailable", None, None
    age = history_age_days(df, now=now)
    as_of = last.strftime("%Y-%m-%d")
    key = normalize_interval(interval)
    if age is None:
        return "unavailable", None, as_of
    if age > _DELISTED_MIN_AGE_DAYS.get(key, 90):
        return "delisted", age, as_of
    if age > _STALE_MAX_AGE_DAYS.get(key, 5):
        return "stale", age, as_of
    return "live", age, as_of
