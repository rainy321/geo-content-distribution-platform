import unittest

from sau_backend import app
from services.request_guard import FixedWindowRateLimiter


class AccessControlApiTests(unittest.TestCase):
    def setUp(self):
        self.original_config = {
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "ACCESS_PASSWORD": app.config.get("ACCESS_PASSWORD"),
            "SECRET_KEY": app.config.get("SECRET_KEY"),
            "SESSION_COOKIE_SECURE": app.config.get("SESSION_COOKIE_SECURE"),
            "AUTH_LOGIN_ATTEMPTS_PER_MINUTE": app.config.get(
                "AUTH_LOGIN_ATTEMPTS_PER_MINUTE"
            ),
            "AUTH_RATE_LIMITER": app.config.get("AUTH_RATE_LIMITER"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            ACCESS_CONTROL_ENABLED=True,
            ACCESS_PASSWORD="correct-horse-battery-staple",
            SECRET_KEY="test-session-secret",
            SESSION_COOKIE_SECURE=False,
            AUTH_LOGIN_ATTEMPTS_PER_MINUTE=5,
            AUTH_RATE_LIMITER=FixedWindowRateLimiter(),
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)

    def test_protects_application_data_but_keeps_health_and_status_public(self):
        dashboard = self.client.get("/api/dashboard")
        health = self.client.get("/api/health")
        status = self.client.get("/api/auth/status")

        self.assertEqual(dashboard.status_code, 401)
        self.assertTrue(
            dashboard.get_json()["data"]["authentication_required"]
        )
        self.assertEqual(health.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["data"]["required"])
        self.assertFalse(status.get_json()["data"]["authenticated"])

    def test_login_sets_http_only_session_and_logout_revokes_access(self):
        rejected = self.client.post(
            "/api/auth/login",
            json={"password": "incorrect"},
        )
        accepted = self.client.post(
            "/api/auth/login",
            json={"password": "correct-horse-battery-staple"},
        )
        dashboard = self.client.get("/api/dashboard")
        logout = self.client.post("/api/auth/logout")
        blocked_again = self.client.get("/api/dashboard")

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        cookie = accepted.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(blocked_again.status_code, 401)

    def test_login_attempts_are_rate_limited(self):
        app.config.update(
            AUTH_LOGIN_ATTEMPTS_PER_MINUTE=1,
            AUTH_RATE_LIMITER=FixedWindowRateLimiter(),
        )

        first = self.client.post(
            "/api/auth/login",
            json={"password": "incorrect"},
        )
        second = self.client.post(
            "/api/auth/login",
            json={"password": "incorrect"},
        )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertGreater(int(second.headers["Retry-After"]), 0)

    def test_security_headers_are_applied_to_public_shell(self):
        response = self.client.get("/")
        try:
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
