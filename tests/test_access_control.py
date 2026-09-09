import unittest
import json
import tempfile
from pathlib import Path

from sau_backend import app
from services.local_auth_service import LocalAuthService
from services.request_guard import FixedWindowRateLimiter


class AccessControlApiTests(unittest.TestCase):
    def setUp(self):
        self.original_config = {
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "ACCESS_PASSWORD": app.config.get("ACCESS_PASSWORD"),
            "DESKTOP_MODE": app.config.get("DESKTOP_MODE"),
            "LOCAL_AUTH_SERVICE": app.config.get("LOCAL_AUTH_SERVICE"),
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
            DESKTOP_MODE=False,
            LOCAL_AUTH_SERVICE=None,
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


class DesktopAccessControlApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temporary_directory.name) / "config" / "auth.json"
        self.auth_service = LocalAuthService(self.auth_path, iterations=100_000)
        self.original_config = {
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "ACCESS_PASSWORD": app.config.get("ACCESS_PASSWORD"),
            "DESKTOP_MODE": app.config.get("DESKTOP_MODE"),
            "LOCAL_AUTH_SERVICE": app.config.get("LOCAL_AUTH_SERVICE"),
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
            ACCESS_PASSWORD="",
            DESKTOP_MODE=True,
            LOCAL_AUTH_SERVICE=self.auth_service,
            SECRET_KEY=self.auth_service.get_session_secret(),
            SESSION_COOKIE_SECURE=False,
            AUTH_LOGIN_ATTEMPTS_PER_MINUTE=8,
            AUTH_RATE_LIMITER=FixedWindowRateLimiter(),
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temporary_directory.cleanup()

    def test_first_run_setup_unlocks_desktop_without_storing_plaintext(self):
        status = self.client.get("/api/auth/status")
        blocked = self.client.get("/api/dashboard")
        login_before_setup = self.client.post(
            "/api/auth/login", json={"password": "Geo-Desktop-2026!"}
        )
        weak_setup = self.client.post(
            "/api/auth/setup", json={"password": "too-short"}
        )
        accepted = self.client.post(
            "/api/auth/setup", json={"password": "Geo-Desktop-2026!"}
        )
        dashboard = self.client.get("/api/dashboard")

        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["data"]["setup_required"])
        self.assertEqual(status.get_json()["data"]["mode"], "desktop_local")
        self.assertEqual(blocked.status_code, 401)
        self.assertTrue(blocked.get_json()["data"]["setup_required"])
        self.assertEqual(login_before_setup.status_code, 409)
        self.assertEqual(weak_setup.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(dashboard.status_code, 200)

        persisted = self.auth_path.read_text(encoding="utf-8")
        self.assertNotIn("Geo-Desktop-2026!", persisted)
        self.assertIsInstance(json.loads(persisted)["password"], dict)

    def test_setup_is_one_time_and_password_login_survives_logout(self):
        first = self.client.post(
            "/api/auth/setup", json={"password": "Geo-Desktop-2026!"}
        )
        repeated = self.client.post(
            "/api/auth/setup", json={"password": "Other-Desktop-2026!"}
        )
        logout = self.client.post("/api/auth/logout")
        wrong = self.client.post(
            "/api/auth/login", json={"password": "Other-Desktop-2026!"}
        )
        accepted = self.client.post(
            "/api/auth/login", json={"password": "Geo-Desktop-2026!"}
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(accepted.status_code, 200)

    def test_password_change_requires_session_and_current_password(self):
        self.auth_service.set_initial_password("Geo-Desktop-2026!")
        unauthenticated = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Geo-Desktop-2026!",
                "new_password": "Geo-Desktop-Next-2026!",
            },
        )
        self.client.post(
            "/api/auth/login", json={"password": "Geo-Desktop-2026!"}
        )
        wrong_current = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "wrong-current",
                "new_password": "Geo-Desktop-Next-2026!",
            },
        )
        changed = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Geo-Desktop-2026!",
                "new_password": "Geo-Desktop-Next-2026!",
            },
        )
        self.client.post("/api/auth/logout")
        old_login = self.client.post(
            "/api/auth/login", json={"password": "Geo-Desktop-2026!"}
        )
        new_login = self.client.post(
            "/api/auth/login", json={"password": "Geo-Desktop-Next-2026!"}
        )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(wrong_current.status_code, 401)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)


if __name__ == "__main__":
    unittest.main()
