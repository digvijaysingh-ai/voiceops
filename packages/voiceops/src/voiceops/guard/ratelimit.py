"""In-process sliding-window rate limiter (per key, per minute).

Per process: behind several workers each has its own window. Enough to stop a
runaway agent or a scripted upload loop; not a substitute for an edge limiter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

WINDOW_S = 60


class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Records the hit and returns True, or returns False when the key is over its limit."""
        now = self.clock()
        q = self.hits[key]
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True
