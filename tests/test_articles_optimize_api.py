import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.ai_service import AIConfigurationError, AIServiceError, AISettings


class ArticleOptimizationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    "INSERT INTO projects (name, keywords) VALUES (?, ?)",
                    (
                        "XX科技",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                    ),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, summary, content, tags, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.project_id,
                        "AI Agent",
                        "原摘要",
                        "XX科技的简短正文。",
                        json.dumps(["AI Agent"], ensure_ascii=False),
                        "draft",
                    ),
                ).lastrowid

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    @patch("sau_backend.optimize_geo_content")
    def test_returns_preview_scores_without_overwriting_article(self, mock_optimize):
        long_text = "企业应依据真实业务需求推进，并定期核对内容中的事实。" * 16
        mock_optimize.return_value = {
            "title": "企业 AI Agent 实施指南",
            "summary": "优化摘要",
            "content": f"""XX科技提供企业 AI Agent 相关服务。

## 实施步骤
{long_text}

## FAQ
问：企业智能体适合谁？
答：适合已有明确业务目标的企业。

## 总结
企业应结合自身情况评估。来源待补充。
""",
            "tags": ["AI Agent", "企业智能体"],
            "faq": [],
        }

        response = self.client.post(f"/api/articles/{self.article_id}/optimize")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["optimized"]["title"], "企业 AI Agent 实施指南")
        self.assertGreater(data["after"]["score"], data["before"]["score"])
        call = mock_optimize.call_args.kwargs
        self.assertEqual(call["article"]["id"], self.article_id)
        self.assertEqual(call["score"], data["before"])

        stored = self.client.get(f"/api/articles/{self.article_id}").get_json()["data"]
        self.assertEqual(stored["title"], "AI Agent")
        self.assertEqual(stored["content"], "XX科技的简短正文。")

    def test_returns_not_found_for_unknown_article(self):
        response = self.client.post("/api/articles/999999/optimize")
        self.assertEqual(response.status_code, 404)

    @patch("sau_backend.optimize_geo_content")
    def test_optimization_accepts_request_scoped_ai_config(self, mock_optimize):
        mock_optimize.return_value = {
            "title": "优化标题",
            "summary": "优化摘要",
            "content": "XX科技提供企业智能体服务。",
            "tags": ["企业智能体"],
            "faq": [],
        }

        response = self.client.post(
            f"/api/articles/{self.article_id}/optimize",
            json={
                "ai_config": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "browser-session-secret",
                    "model": "custom-model",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = mock_optimize.call_args.kwargs["settings"]
        self.assertIsInstance(settings, AISettings)
        self.assertEqual(settings.model, "custom-model")

    @patch("sau_backend.optimize_geo_content")
    def test_maps_missing_ai_configuration(self, mock_optimize):
        mock_optimize.side_effect = AIConfigurationError("缺少 AI 配置")
        response = self.client.post(f"/api/articles/{self.article_id}/optimize")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], 503)

    @patch("sau_backend.optimize_geo_content")
    def test_maps_provider_failure(self, mock_optimize):
        mock_optimize.side_effect = AIServiceError("AI 服务请求失败")
        response = self.client.post(f"/api/articles/{self.article_id}/optimize")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], 502)


if __name__ == "__main__":
    unittest.main()
