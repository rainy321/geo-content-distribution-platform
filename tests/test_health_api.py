import tempfile
import unittest
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class HealthApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "database.db"
        initialize_database(self.db_path)
        self.original_config = {
            "DATABASE_PATH": app.config["DATABASE_PATH"],
            "DEMO_MODE": app.config.get("DEMO_MODE"),
            "STORAGE_SCOPE": app.config.get("STORAGE_SCOPE"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            DEMO_MODE=True,
            STORAGE_SCOPE="filesystem",
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def test_reports_ready_database_without_exposing_paths(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["data"]["status"], "ok")
        self.assertEqual(payload["data"]["database"], "ok")
        self.assertTrue(payload["data"]["demo_mode"])
        self.assertEqual(payload["data"]["storage_scope"], "filesystem")
        self.assertIn(payload["data"]["rate_limit_scope"], {"instance", "shared"})
        self.assertFalse(payload["data"]["real_publishing_enabled"])
        self.assertNotIn(str(self.db_path), response.get_data(as_text=True))

    def test_backend_service_prefix_reaches_the_same_api(self):
        response = self.client.get("/backend/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json()["data"]["status"], "ok")

    def test_reports_unhealthy_when_database_cannot_be_opened(self):
        app.config["DATABASE_PATH"] = self.root / "missing" / "database.db"

        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["data"]["database"], "unavailable")


if __name__ == "__main__":
    unittest.main()
