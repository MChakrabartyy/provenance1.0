"""Timeouts and retries for external calls. Transient errors only; never retry a 400."""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """Timeouts, 429s, 5xx: safe to retry."""


class PermanentError(Exception):
    """400s, parse failures: never retry."""


def with_retry(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 0.2, max_delay: float = 5.0,
               sleep: Callable[[float], None] = time.sleep) -> T:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as e:  # noqa: PERF203
            last = e
            if attempt == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2 ** attempt)) * (0.5 + random.random())  # jitter
            sleep(delay)
    assert last is not None
    raise last
