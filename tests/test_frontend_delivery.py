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

    def test_system_settings_supports_persisted_unified_ai_config(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend" / "src"
        router = (frontend_root / "router" / "index.js").read_text(encoding="utf-8")
        app = (frontend_root / "App.vue").read_text(encoding="utf-8")
        article_api = (frontend_root / "api" / "article.js").read_text(
            encoding="utf-8"
        )
        ai_config_api = (frontend_root / "api" / "aiConfig.js").read_text(
            encoding="utf-8"
        )
        config = (frontend_root / "utils" / "aiConfig.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("path: '/settings'", router)
        self.assertIn("<span>系统设置</span>", app)
        self.assertNotIn("withCustomAIConfig", article_api)
        self.assertIn("http.put('/api/ai/config'", ai_config_api)
        self.assertIn("http.post('/api/ai/config/test'", ai_config_api)
        self.assertIn("apiKeyConfigured", config)
        self.assertNotIn("sessionStorage", config)

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
        self.assertIn("管理员口令", gate)
        self.assertIn("authApi.setup(password.value)", gate)
        self.assertIn("authApi.login(password.value)", gate)
        self.assertIn("'new-password' : 'current-password'", gate)
        self.assertIn('autocomplete="new-password"', gate)
        self.assertNotIn("localStorage", gate)
        self.assertIn("withCredentials: true", request)
        self.assertIn("geo-auth-required", request)
        self.assertIn("|| '/backend'", api_config)
        self.assertIn("'/backend':", vite_config)
        self.assertNotIn("path.replace(/^\\/api/", vite_config)

    def test_review_monitor_and_account_risks_are_visible_without_batch_modal(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend" / "src"
        app = (frontend_root / "App.vue").read_text(encoding="utf-8")
        accounts = (frontend_root / "views" / "AccountManagement.vue").read_text(
            encoding="utf-8"
        )
        distribution = (frontend_root / "views" / "DistributionCenter.vue").read_text(
            encoding="utf-8"
        )

        self.assertIn("publishApi.getReviewMonitor", app)
        self.assertIn("global-review-alerts", app)
        self.assertIn("30_000", app)
        self.assertIn("critical-risk-overview", accounts)
        self.assertIn("有账号已停止自动发布", accounts)
        self.assertIn("account.risk_event?.frozen === true", accounts)
        self.assertIn("publish-risk-overview", distribution)
        self.assertIn("job.account_risk?.frozen", distribution)
        self.assertIn("job.review_monitor", distribution)
        self.assertIn("后台只读监测中", distribution)
        self.assertIn("publishApi.rearmReviewMonitor", distribution)
        self.assertIn("不会重新提交文章", distribution)

    def test_multi_model_visibility_panel_recovers_without_post_retry(self):
        frontend_root = Path(__file__).resolve().parents[1] / "sau_frontend" / "src"
        view = (frontend_root / "views" / "AiVisibility.vue").read_text(
            encoding="utf-8"
        )
        api = (frontend_root / "api" / "diagnostics.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("getVisibilityPanel(requestId", api)
        self.assertIn("/api/geo/visibility/sample-panel/", api)
        self.assertIn("mode: multiModel ? 'panel' : 'single'", view)
        self.assertIn("pollPendingPanel", view)
        self.assertIn("diagnosticsApi.getVisibilityPanel", view)
        self.assertIn("pending.mode === 'panel'", view)
        self.assertIn("SAMPLING_RESUME_MAX_MS", view)
        self.assertIn("已停止自动查询并保留请求身份", view)
        self.assertIn("不会重复调用", view)


if __name__ == "__main__":
    unittest.main()
