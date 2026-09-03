import unittest
from unittest.mock import Mock

import requests

from services.request_guard import (
    FixedWindowRateLimiter,
    RateLimitConfigurationError,
    UpstashRateLimiter,
    create_rate_limiter_from_environment,
)


class FixedWindowRateLimiterTests(unittest.TestCase):
    def test_allows_up_to_limit_and_reports_retry_after(self):
        limiter = FixedWindowRateLimiter(window_seconds=60)

        first = limiter.consume("client", limit=2, now=120.0)
        second = limiter.consume("client", limit=2, now=121.0)
        blocked = limiter.consume("client", limit=2, now=122.0)

        self.assertTrue(first.allowed)
        self.assertEqual(first.remaining, 1)
        self.assertTrue(second.allowed)
        self.assertEqual(second.remaining, 0)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after_seconds, 58)

    def test_new_window_resets_counter(self):
        limiter = FixedWindowRateLimiter(window_seconds=60)
        limiter.consume("client", limit=1, now=59.0)

        decision = limiter.consume("client", limit=1, now=60.0)

        self.assertTrue(decision.allowed)

    def test_non_positive_limit_disables_limiter(self):
        limiter = FixedWindowRateLimiter()

        for _ in range(20):
            self.assertTrue(limiter.consume("client", limit=0).allowed)


class UpstashRateLimiterTests(unittest.TestCase):
    def _limiter(self, *, result=None, side_effect=None):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": result or [1, 1]}
        http_client = Mock()
        http_client.post.side_effect = side_effect
        if side_effect is None:
            http_client.post.return_value = response
        limiter = UpstashRateLimiter(
            rest_url="https://example.upstash.io",
            token="secret-token",
            http_client=http_client,
        )
        return limiter, http_client

    def test_executes_atomic_script_without_exposing_client_key(self):
        limiter, http_client = self._limiter(result=[1, 1])

        decision = limiter.consume("ai:203.0.113.42", limit=2, now=120.0)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.remaining, 1)
        request = http_client.post.call_args
        command = request.kwargs["json"]
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(command[2], "1")
        self.assertNotIn("203.0.113.42", command[3])
        self.assertEqual(command[4], 2)
        self.assertEqual(request.kwargs["timeout"], 2.0)

    def test_reports_shared_limit_and_retry_after(self):
        limiter, _http_client = self._limiter(result=[0, 2])

        decision = limiter.consume("client", limit=2, now=122.0)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.remaining, 0)
        self.assertEqual(decision.retry_after_seconds, 58)

    def test_uses_warmed_local_limiter_when_upstash_is_unavailable(self):
        limiter, http_client = self._limiter(result=[1, 1])
        first = limiter.consume("client", limit=1, now=120.0)
        http_client.post.side_effect = requests.Timeout("provider timeout")

        second = limiter.consume("client", limit=1, now=121.0)

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)

    def test_rejects_non_upstash_or_credentialed_url(self):
        invalid_urls = (
            "http://example.upstash.io",
            "https://example.com",
            "https://user:pass@example.upstash.io",
        )
        for rest_url in invalid_urls:
            with self.subTest(rest_url=rest_url):
                with self.assertRaises(RateLimitConfigurationError):
                    UpstashRateLimiter(rest_url=rest_url, token="token")


class RateLimiterEnvironmentTests(unittest.TestCase):
    def test_uses_instance_limiter_when_shared_config_is_absent(self):
        limiter = create_rate_limiter_from_environment({})

        self.assertIsInstance(limiter, FixedWindowRateLimiter)
        self.assertEqual(limiter.scope, "instance")

    def test_uses_shared_limiter_when_both_values_are_present(self):
        limiter = create_rate_limiter_from_environment(
            {
                "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
                "UPSTASH_REDIS_REST_TOKEN": "token",
            }
        )

        self.assertIsInstance(limiter, UpstashRateLimiter)
        self.assertEqual(limiter.scope, "shared")

    def test_rejects_partial_shared_configuration(self):
        with self.assertRaisesRegex(
            RateLimitConfigurationError,
            "UPSTASH_REDIS_REST_TOKEN",
        ):
            create_rate_limiter_from_environment(
                {
                    "UPSTASH_REDIS_REST_URL": (
                        "https://example.upstash.io"
                    )
                }
            )


if __name__ == "__main__":
    unittest.main()
