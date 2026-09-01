import tempfile
import unittest
from pathlib import Path

from sau_backend import app


class FrontendDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.build_dir = Path(self.temp_dir.name)
        (self.build_dir / "assets").mkdir()
        (self.build_dir / "index.html").write_text(
            "<!doctype html><title>GEO Shell</title><main>GEO APP</main>",
            encoding="utf-8",
        )
        (self.build_dir / "assets" / "main.js").write_text(
            "window.__geoLoaded = true",
            encoding="utf-8",
        )
        self.original_build_dir = app.config["FRONTEND_BUILD_DIR"]
        self.original_testing = app.testing
        app.config.update(FRONTEND_BUILD_DIR=self.build_dir, TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(
            FRONTEND_BUILD_DIR=self.original_build_dir,
            TESTING=self.original_testing,
        )
        self.temp_dir.cleanup()

    def test_serves_root_and_spa_history_routes(self):
        root = self.client.get("/")
        history_route = self.client.get("/project-management")
        try:
            self.assertEqual(root.status_code, 200)
            self.assertEqual(history_route.status_code, 200)
            self.assertIn("GEO APP", root.get_data(as_text=True))
            self.assertIn("GEO APP", history_route.get_data(as_text=True))
        finally:
            root.close()
            history_route.close()

    def test_serves_built_assets(self):
        response = self.client.get("/assets/main.js")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("__geoLoaded", response.get_data(as_text=True))
        finally:
            response.close()

    def test_unknown_api_is_json_404_not_spa_html(self):
        response = self.client.get("/api/not-a-real-endpoint")
        try:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["msg"], "API 不存在")
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
