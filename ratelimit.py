"""Simple in-memory per-user rate limiter for game API endpoints."""

import time
from collections import defaultdict

_limits: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(key: str, max_calls: int = 5, window: float = 1.0) -> bool:
    """
    Return True if the caller is within the rate limit (max_calls per window seconds).
    Returns False if they should be throttled.
    """
    now = time.time()
    cutoff = now - window
    timestamps = _limits[key]
    # Keep only recent timestamps
    _limits[key] = [t for t in timestamps if t > cutoff]
    if len(_limits[key]) >= max_calls:
        return False
    _limits[key].append(now)
    return True
