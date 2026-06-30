"""Interval and period mapping across market data providers."""

from datetime import datetime, timedelta, timezone

# yfinance interval -> (polygon multiplier, polygon timespan) | alpha_vantage interval
INTERVAL_MAP: dict[str, dict[str, str | tuple[int, str]]] = {
    "1m": {"polygon": (1, "minute"), "alpha_vantage": "1min"},
    "2m": {"polygon": (2, "minute"), "alpha_vantage": "1min"},
    "5m": {"polygon": (5, "minute"), "alpha_vantage": "5min"},
    "15m": {"polygon": (15, "minute"), "alpha_vantage": "15min"},
    "30m": {"polygon": (30, "minute"), "alpha_vantage": "30min"},
    "60m": {"polygon": (1, "hour"), "alpha_vantage": "60min"},
    "1h": {"polygon": (1, "hour"), "alpha_vantage": "60min"},
    "4h": {"polygon": (4, "hour"), "alpha_vantage": "60min"},
    "1d": {"polygon": (1, "day"), "alpha_vantage": "daily"},
    "1wk": {"polygon": (1, "week"), "alpha_vantage": "weekly"},
    "1mo": {"polygon": (1, "month"), "alpha_vantage": "monthly"},
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
