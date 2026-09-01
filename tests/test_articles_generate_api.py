import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.ai_service import AIConfigurationError, AIServiceError


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

    @patch("sau_backend.generate_geo_content")
    def test_generates_article_from_project_and_stored_keywords(self, mock_generate):
        mock_generate.return_value = {
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
        call = mock_generate.call_args.kwargs
        self.assertEqual(call["project"]["name"], "XX科技")
        self.assertEqual(call["project"]["competitors"], ["品牌A"])
        self.assertEqual(call["keywords"], ["AI Agent", "企业智能体"])
        self.assertEqual(call["target_platform"], "知乎")

    @patch("sau_backend.generate_geo_content")
    def test_request_keywords_override_project_keywords(self, mock_generate):
        mock_generate.return_value = {
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
            mock_generate.call_args.kwargs["keywords"],
            ["大模型应用"],
        )

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

    @patch("sau_backend.generate_geo_content")
    def test_maps_missing_ai_configuration_to_service_unavailable(
        self,
        mock_generate,
    ):
        mock_generate.side_effect = AIConfigurationError("缺少 AI 配置")

        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": self.project_id, "topic": "测试主题"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], 503)

    @patch("sau_backend.generate_geo_content")
    def test_maps_provider_error_to_bad_gateway(self, mock_generate):
        mock_generate.side_effect = AIServiceError("AI 服务请求失败")

        response = self.client.post(
            "/api/articles/generate",
            json={"project_id": self.project_id, "topic": "测试主题"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], 502)


if __name__ == "__main__":
    unittest.main()
