"""Tiny in-memory sliding-window rate limiter (per process)."""

import threading
import time
from collections import defaultdict, deque

_LOCK = threading.Lock()
_HITS = defaultdict(deque)


def hit(key: str, limit: int, window: int) -> bool:
    """Record a hit. Returns False (and records nothing) when the limit is exceeded."""
    now = time.time()
    with _LOCK:
        q = _HITS[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(_HITS) > 20000:  # purge idle keys
            for k in [k for k, v in _HITS.items() if not v or v[-1] <= now - 3600]:
                _HITS.pop(k, None)
        return True


def reset(key: str):
    with _LOCK:
        _HITS.pop(key, None)
