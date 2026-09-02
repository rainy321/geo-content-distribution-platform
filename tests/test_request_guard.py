import unittest

from services.request_guard import FixedWindowRateLimiter


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


if __name__ == "__main__":
    unittest.main()
