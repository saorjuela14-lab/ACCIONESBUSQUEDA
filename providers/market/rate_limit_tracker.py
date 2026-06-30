"""Rate limit tracking for market data providers."""

from datetime import date, datetime, timezone

from utils.logging import get_logger

logger = get_logger(__name__)


class RateLimitTracker:
    """Tracks per-provider daily and per-minute request quotas."""

    def __init__(self) -> None:
        self._daily: dict[str, dict[str, int]] = {}
        self._minute_timestamps: dict[str, list[float]] = {}

    def _today(self) -> str:
        return date.today().isoformat()

    def can_request(
        self,
        provider: str,
        daily_limit: int | None = None,
        per_minute_limit: int | None = None,
    ) -> bool:
        if daily_limit is not None:
            count = self._daily.get(provider, {}).get(self._today(), 0)
            if count >= daily_limit:
                logger.info("ratelimit.daily.exhausted", provider=provider, count=count, limit=daily_limit)
                return False

        if per_minute_limit is not None:
            now = datetime.now(timezone.utc).timestamp()
            window = self._minute_timestamps.get(provider, [])
            window = [t for t in window if now - t < 60]
            self._minute_timestamps[provider] = window
            if len(window) >= per_minute_limit:
                logger.info("ratelimit.minute.exhausted", provider=provider, count=len(window), limit=per_minute_limit)
                return False

        return True

    def record(self, provider: str) -> None:
        today = self._today()
        if provider not in self._daily:
            self._daily[provider] = {}
        self._daily[provider][today] = self._daily[provider].get(today, 0) + 1

        now = datetime.now(timezone.utc).timestamp()
        if provider not in self._minute_timestamps:
            self._minute_timestamps[provider] = []
        self._minute_timestamps[provider].append(now)

    def get_usage(self, provider: str) -> dict[str, int]:
        return {
            "daily": self._daily.get(provider, {}).get(self._today(), 0),
            "minute_window": len(self._minute_timestamps.get(provider, [])),
        }

    def reset(self) -> None:
        self._daily.clear()
        self._minute_timestamps.clear()


_tracker: RateLimitTracker | None = None


def get_rate_limit_tracker() -> RateLimitTracker:
    global _tracker
    if _tracker is None:
        _tracker = RateLimitTracker()
    return _tracker
