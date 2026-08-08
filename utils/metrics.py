"""In-process counters for simple monitoring."""

from __future__ import annotations

import threading
import time
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._started = time.time()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
        return {
            "uptime_seconds": int(time.time() - self._started),
            "counters": counters,
        }


metrics = MetricsRegistry()
