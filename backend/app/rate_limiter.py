"""Client-side rate limiting so the pipeline throttles itself before Gemini
or the Google Fact Check Tools API rejects requests.

A simple sliding-window counter, not a full token bucket — the pipeline is
single-process and low-volume, so this is deliberately minimal. `acquire()`
blocks (sleeping) until a slot frees up, then reserves it.
"""

import threading
import time
from collections import deque

import config


class RateLimiter:
    """Blocks the caller so no more than `max_calls` happen per `period` seconds."""

    def __init__(self, max_calls: int, period: float = 60.0):
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.period = period
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.period - (now - self._calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)


# Gemini Flash free tier: 15 requests/minute by default (see classifier.py
# docstring). Override with GEMINI_RPM if your quota differs.
gemini_limiter = RateLimiter(max_calls=config.GEMINI_RPM, period=60.0)

# Google Fact Check Tools API has no published per-minute cap; FACT_CHECK_RPM
# just keeps us comfortably inside the free daily quota.
fact_check_limiter = RateLimiter(max_calls=config.FACT_CHECK_RPM, period=60.0)