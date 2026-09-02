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
        (self.build_dir / "geo-favicon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><title>GEO</title></svg>',
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

    def test_legacy_favicon_route_serves_geo_brand_icon(self):
        response = self.client.get("/favicon.ico")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/svg+xml")
            self.assertIn("<title>GEO</title>", response.get_data(as_text=True))
        finally:
            response.close()

    def test_unknown_api_is_json_404_not_spa_html(self):
        response = self.client.get("/api/not-a-real-endpoint")
        try:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["msg"], "API 不存在")
        finally:
            response.close()

    def test_distribution_job_polling_is_serial_and_stops_after_unmount(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "sau_frontend"
            / "src"
            / "views"
            / "DistributionCenter.vue"
        ).read_text(encoding="utf-8")

        self.assertNotIn("window.setInterval", source)
        self.assertIn("await fetchJobs(true)", source)
        self.assertIn("scheduleJobPoll()", source)
        self.assertIn("pollingStopped = true", source)
        self.assertIn("window.clearTimeout(pollTimer)", source)

    def test_frontend_shell_uses_geo_brand_metadata(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend"
        index = (frontend_root / "index.html").read_text(encoding="utf-8")

        self.assertIn('<html lang="zh-CN">', index)
        self.assertIn("<title>GEO 智能内容分发平台</title>", index)
        self.assertIn('href="/geo-favicon.svg"', index)
        self.assertTrue((frontend_root / "public" / "geo-favicon.svg").is_file())
        self.assertNotIn("SAU自媒体自动化运营系统", index)

    def test_system_settings_supports_session_scoped_custom_ai_config(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend" / "src"
        router = (frontend_root / "router" / "index.js").read_text(encoding="utf-8")
        app = (frontend_root / "App.vue").read_text(encoding="utf-8")
        article_api = (frontend_root / "api" / "article.js").read_text(
            encoding="utf-8"
        )
        config = (frontend_root / "utils" / "aiConfig.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("path: '/settings'", router)
        self.assertIn("<span>系统设置</span>", app)
        self.assertIn("withCustomAIConfig(data)", article_api)
        self.assertIn("withCustomAIConfig()", article_api)
        self.assertIn("window.sessionStorage.setItem(SESSION_SECRET_KEY", config)
        self.assertNotIn("localStorage.setItem(SESSION_SECRET_KEY", config)

    def test_frontend_has_operator_access_gate_and_credentialed_requests(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend"
        app = (frontend_root / "src" / "App.vue").read_text(encoding="utf-8")
        gate = (frontend_root / "src" / "components" / "AccessGate.vue").read_text(
            encoding="utf-8"
        )
        request = (frontend_root / "src" / "utils" / "request.js").read_text(
            encoding="utf-8"
        )
        api_config = (frontend_root / "src" / "config" / "api.js").read_text(
            encoding="utf-8"
        )
        vite_config = (frontend_root / "vite.config.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("<AccessGate", app)
        self.assertIn("运营访问口令", gate)
        self.assertIn('autocomplete="current-password"', gate)
        self.assertNotIn("localStorage", gate)
        self.assertIn("withCredentials: true", request)
        self.assertIn("geo-auth-required", request)
        self.assertIn("|| '/backend'", api_config)
        self.assertIn("'/backend':", vite_config)
        self.assertNotIn("path.replace(/^\\/api/", vite_config)


if __name__ == "__main__":
    unittest.main()
