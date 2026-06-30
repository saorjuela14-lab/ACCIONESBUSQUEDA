"""US market hours utilities."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
PRE_MARKET_START = time(8, 0)
POST_MARKET_END = time(18, 0)

# US market holidays 2026 (simplified set)
US_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}


def now_et() -> datetime:
    return datetime.now(US_EASTERN)


def is_trading_day(dt: datetime | None = None) -> bool:
    dt = dt or now_et()
    if dt.weekday() >= 5:
        return False
    return dt.strftime("%Y-%m-%d") not in US_HOLIDAYS_2026


def is_market_open(dt: datetime | None = None) -> bool:
    dt = dt or now_et()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def is_extended_hours(dt: datetime | None = None) -> bool:
    """Pre-market or post-market extended session."""
    dt = dt or now_et()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return PRE_MARKET_START <= t < MARKET_OPEN or MARKET_CLOSE <= t < POST_MARKET_END


def should_run_automation(dt: datetime | None = None) -> bool:
    """Run watchlist scans during market + extended hours on trading days."""
    dt = dt or now_et()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    return PRE_MARKET_START <= t < POST_MARKET_END
