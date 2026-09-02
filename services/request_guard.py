from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class FixedWindowRateLimiter:
    """Small process-local limiter used as an application safety net.

    Vercel functions may run in more than one region or instance, so this is not
    a replacement for a shared Redis/WAF limiter. It still bounds accidental
    bursts inside each running process and keeps local/self-hosted deployments
    safe by default when a limit is configured.
    """

    def __init__(self, *, window_seconds: int = 60, max_buckets: int = 4096):
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须是正整数")
        if max_buckets <= 0:
            raise ValueError("max_buckets 必须是正整数")
        self.window_seconds = int(window_seconds)
        self.max_buckets = int(max_buckets)
        self._buckets: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def consume(
        self,
        key: str,
        *,
        limit: int,
        now: float | None = None,
    ) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(True, 0, 0)

        current = time.time() if now is None else float(now)
        window = int(current // self.window_seconds)
        bucket_key = str(key or "unknown")

        with self._lock:
            stored_window, count = self._buckets.get(bucket_key, (window, 0))
            if stored_window != window:
                count = 0
            if count >= limit:
                retry_after = max(
                    1,
                    int(((window + 1) * self.window_seconds) - current + 0.999),
                )
                self._buckets[bucket_key] = (window, count)
                return RateLimitDecision(False, 0, retry_after)

            count += 1
            self._buckets[bucket_key] = (window, count)
            if len(self._buckets) > self.max_buckets:
                self._prune(window)
            return RateLimitDecision(True, max(0, limit - count), 0)

    def _prune(self, current_window: int) -> None:
        expired = [
            key
            for key, (window, _count) in self._buckets.items()
            if window < current_window
        ]
        for key in expired:
            self._buckets.pop(key, None)
        if len(self._buckets) <= self.max_buckets:
            return
        overflow = len(self._buckets) - self.max_buckets
        for key in list(self._buckets)[:overflow]:
            self._buckets.pop(key, None)
