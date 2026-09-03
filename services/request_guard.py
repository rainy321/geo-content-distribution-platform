from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

import requests


logger = logging.getLogger("geo.request_guard")


class RateLimitConfigurationError(RuntimeError):
    """Raised when an optional shared rate limiter is only partly configured."""


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

    scope = "instance"

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


class UpstashRateLimiter:
    """Atomic shared fixed-window limiter backed by the Upstash REST API.

    A local limiter is kept warm on every successful shared decision. If the
    REST service is temporarily unavailable, requests remain bounded within
    the current process instead of failing the whole protected endpoint.
    """

    scope = "shared"
    _CONSUME_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
if current >= limit then
  return {0, current}
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return {1, current}
""".strip()

    def __init__(
        self,
        *,
        rest_url: str,
        token: str,
        window_seconds: int = 60,
        timeout_seconds: float = 2.0,
        key_prefix: str = "geo:rate-limit",
        fallback: FixedWindowRateLimiter | None = None,
        http_client=None,
    ):
        normalized_url = str(rest_url or "").strip().rstrip("/")
        parsed_url = urlsplit(normalized_url)
        hostname = (parsed_url.hostname or "").lower()
        if (
            parsed_url.scheme != "https"
            or not hostname.endswith(".upstash.io")
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise RateLimitConfigurationError(
                "UPSTASH_REDIS_REST_URL 必须是无凭据、无查询参数的 "
                "https://*.upstash.io 地址"
            )
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise RateLimitConfigurationError(
                "UPSTASH_REDIS_REST_TOKEN 不能为空"
            )
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须是正整数")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须是正数")

        self.rest_url = normalized_url
        self._token = normalized_token
        self.window_seconds = int(window_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.key_prefix = str(key_prefix or "geo:rate-limit").strip(":")
        self._fallback = fallback or FixedWindowRateLimiter(
            window_seconds=self.window_seconds
        )
        self._http = http_client or requests.Session()

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
        retry_after = max(
            1,
            int(((window + 1) * self.window_seconds) - current + 0.999),
        )
        digest = hashlib.sha256(
            str(key or "unknown").encode("utf-8")
        ).hexdigest()
        redis_key = f"{self.key_prefix}:{window}:{digest}"
        command = [
            "EVAL",
            self._CONSUME_SCRIPT,
            "1",
            redis_key,
            int(limit),
            retry_after + 1,
        ]

        try:
            response = self._http.post(
                self.rest_url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=command,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "error" in payload:
                raise ValueError("Upstash 返回了无效响应")
            result = payload.get("result")
            if not isinstance(result, list) or len(result) != 2:
                raise ValueError("Upstash 返回了无效限流结果")
            allowed = bool(int(result[0]))
            count = int(result[1])
        except (requests.RequestException, TypeError, ValueError) as exc:
            logger.warning(
                "rate_limit.shared_unavailable error_type=%s fallback=instance",
                type(exc).__name__,
            )
            return self._fallback.consume(key, limit=limit, now=current)

        # Mirror successful traffic locally so failover does not start from an
        # empty counter after the shared provider becomes unavailable.
        self._fallback.consume(key, limit=limit, now=current)
        return RateLimitDecision(
            allowed=allowed,
            remaining=max(0, int(limit) - count),
            retry_after_seconds=0 if allowed else retry_after,
        )


def create_rate_limiter_from_environment(
    environ: Mapping[str, str] | None = None,
):
    """Select shared Upstash limiting only when both secrets are present."""

    values = os.environ if environ is None else environ
    rest_url = str(values.get("UPSTASH_REDIS_REST_URL", "")).strip()
    token = str(values.get("UPSTASH_REDIS_REST_TOKEN", "")).strip()
    if not rest_url and not token:
        return FixedWindowRateLimiter()
    if not rest_url or not token:
        missing = (
            "UPSTASH_REDIS_REST_URL" if not rest_url
            else "UPSTASH_REDIS_REST_TOKEN"
        )
        raise RateLimitConfigurationError(
            f"共享限流配置不完整：缺少 {missing}"
        )
    return UpstashRateLimiter(rest_url=rest_url, token=token)
