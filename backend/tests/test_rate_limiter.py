import time

import pytest

from rate_limiter import RateLimiter


def test_rejects_a_non_positive_call_budget():
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0)


def test_calls_within_budget_do_not_block():
    limiter = RateLimiter(max_calls=3, period=10.0)

    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.1


def test_exceeding_the_budget_blocks_until_the_window_frees_up():
    limiter = RateLimiter(max_calls=2, period=0.3)

    limiter.acquire()
    limiter.acquire()
    start = time.monotonic()
    limiter.acquire()  # third call must wait for the window to slide
    elapsed = time.monotonic() - start

    assert elapsed >= 0.2
