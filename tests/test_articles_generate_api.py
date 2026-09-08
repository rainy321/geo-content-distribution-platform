import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.ai_service import AISettings
from services.content_engine_contract import (
    ContentEngineConfigurationError,
    ContentEngineProtocolError,
)
from services.request_guard import FixedWindowRateLimiter


class ArticleGenerationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "XX科技",
                        "https://example.test",
                        "企业 AI Agent",
                        "人工智能",
                        "帮助企业构建智能服务流程。",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                        json.dumps(["品牌A"], ensure_ascii=False),
                    ),
                )
                self.project_id = cursor.lastrowid

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    @patch("sau_backend._create_content_generation_service")
    def test_generates_article_from_project_and_stored_keywords(self, mock_factory):
        mock_factory.return_value.generate.return_value = {
            "title": "企业 AI Agent 如何落地",
            "summary": "摘要",
            "content": "正文",
            "tags": ["AI Agent"],
            "faq": [],
        }

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "企业 AI Agent 落地",
                "length": 1000,
                "content_type": "行业科普",
                "target_platform": "知乎",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["title"], "企业 AI Agent 如何落地")
        generation_request = mock_factory.return_value.generate.call_args.args[0]
        self.assertEqual(generation_request.project_context["name"], "XX科技")
        self.assertEqual(generation_request.project_context["competitors"], ["品牌A"])
        self.assertEqual(generation_request.keywords, ("AI Agent", "企业智能体"))
        self.assertEqual(generation_request.target_platform, "zhihu")

    @patch("sau_backend._create_content_generation_service")
    def test_request_keywords_override_project_keywords(self, mock_factory):
        mock_factory.return_value.generate.return_value = {
            "title": "标题",
            "summary": "",
            "content": "正文",
            "tags": ["大模型应用"],
            "faq": [],
        }

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "测试主题",
                "keywords": [" 大模型应用 ", "大模型应用", ""],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_factory.return_value.generate.call_args.args[0].keywords,
            ("大模型应用",),
        )

    @patch("sau_backend._create_content_generation_service")
    def test_request_scoped_ai_config_overrides_server_defaults(self, mock_factory):
        mock_factory.return_value.generate.return_value = {
            "title": "自定义模型标题",
            "summary": "",
            "content": "正文",
            "tags": [],
            "faq": [],
        }
        custom_key = "browser-session-secret"

        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "测试自定义模型",
                "ai_config": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": custom_key,
                    "model": "custom-model",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = mock_factory.call_args.args[0]
        self.assertIsInstance(settings, AISettings)
        self.assertEqual(settings.base_url, "https://api.example.com/v1")
        self.assertEqual(settings.api_key, custom_key)
        self.assertEqual(settings.model, "custom-model")
        self.assertNotIn(custom_key, response.get_data(as_text=True))

    @patch("sau_backend._create_content_generation_service")
    def test_rejects_incomplete_custom_ai_config_before_provider_call(
        self,
        mock_generate,
    ):
        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "测试自定义模型",
                "ai_config": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "",
                    "model": "custom-model",
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("api_key", response.get_json()["msg"])
        mock_generate.assert_not_called()

    def test_ai_config_status_only_reports_capabilities(self):
        with patch.dict(
            "os.environ",
            {
                "AI_BASE_URL": "https://api.example.com/v1",
                "AI_API_KEY": "server-secret",
                "AI_MODEL": "server-model",
            },
            clear=True,
        ):
            response = self.client.get("/api/ai/config-status")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["data"]["server_configured"])
        self.assertEqual(body["data"]["custom_key_storage"], "browser_session")
        serialized = response.get_data(as_text=True)
        self.assertNotIn("server-secret", serialized)
        self.assertNotIn("api.example.com", serialized)

    def test_rejects_missing_topic_before_calling_ai(self):
        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": self.project_id},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["msg"], "文章主题不能为空")

    def test_returns_not_found_for_unknown_project(self):
        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": 999999, "topic": "测试主题"},
        )

        self.assertEqual(response.status_code, 404)

    def test_rejects_non_list_keywords(self):
        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "测试主题",
                "keywords": "AI Agent",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("keywords", response.get_json()["msg"])

    @patch("sau_backend._create_content_generation_service")
    def test_rejects_unknown_target_platform_before_provider_call(self, mock_generate):
        response = self.client.post(
            "/api/articles/generate",
            json={
                "project_id": self.project_id,
                "topic": "测试主题",
                "target_platform": "不存在的平台",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("目标平台", response.get_json()["msg"])
        mock_generate.assert_not_called()

    @patch("sau_backend._create_content_generation_service")
    def test_maps_missing_ai_configuration_to_service_unavailable(
        self,
        mock_generate,
    ):
        mock_generate.side_effect = ContentEngineConfigurationError("缺少 AI 配置")

        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": self.project_id, "topic": "测试主题"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], 503)

    @patch("sau_backend._create_content_generation_service")
    def test_maps_provider_error_to_bad_gateway(self, mock_generate):
        mock_generate.return_value.generate.side_effect = ContentEngineProtocolError(
            "AI 服务请求失败"
        )

        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": self.project_id, "topic": "测试主题"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], 502)

    @patch("sau_backend._create_content_generation_service")
    def test_ai_generation_has_configurable_per_client_rate_limit(
        self,
        mock_generate,
    ):
        mock_generate.return_value.generate.return_value = {
            "title": "标题",
            "summary": "",
            "content": "正文",
            "tags": [],
            "faq": [],
        }
        original_limit = app.config.get("AI_RATE_LIMIT_PER_MINUTE")
        original_limiter = app.config.get("AI_RATE_LIMITER")
        app.config.update(
            AI_RATE_LIMIT_PER_MINUTE=1,
            AI_RATE_LIMITER=FixedWindowRateLimiter(),
        )
        try:
            first = self.client.post(
                "/api/articles/generate",
                json={"project_id": self.project_id, "topic": "第一次请求"},
            )
            second = self.client.post(
                "/api/articles/generate",
                json={"project_id": self.project_id, "topic": "第二次请求"},
            )
        finally:
            app.config.update(
                AI_RATE_LIMIT_PER_MINUTE=original_limit,
                AI_RATE_LIMITER=original_limiter,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["code"], 429)
        self.assertEqual(second.headers["X-RateLimit-Limit"], "1")
        self.assertEqual(mock_generate.return_value.generate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
