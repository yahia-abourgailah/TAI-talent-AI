"""Rate limits for the public endpoints (API plan section 3). Proposed numbers, until D-WEB-3.

Counts are kept per client address in this process's memory, in fixed windows. The address is
only a key here: it is hashed, never logged and never stored. With more than one API process
behind a load balancer each keeps its own counts, so the real limit is the sum; a shared store
(the Redis in the stack) replaces this when the API runs more than one process.
"""

import hashlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    requests: int
    window_seconds: int


UPLOADS = Rule("cv_uploads", requests=10, window_seconds=600)
STATUS_CHECKS = Rule("cv_upload_status", requests=120, window_seconds=60)
APPLICATIONS = Rule("public_applications", requests=20, window_seconds=600)

_MAX_KEYS = 50_000


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = threading.Lock()

    def check(self, rule: Rule, client: str) -> int | None:
        """Counts one request. None when allowed, else the seconds to wait (Retry-After)."""
        now = self._clock()
        window = int(now // rule.window_seconds)
        key = (rule.name, hashlib.sha256(client.encode("utf-8")).hexdigest(), window)
        with self._lock:
            if len(self._counts) > _MAX_KEYS:
                self._counts = {k: v for k, v in self._counts.items() if k[2] >= window}
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
        if count <= rule.requests:
            return None
        return max(1, math.ceil((window + 1) * rule.window_seconds - now))
