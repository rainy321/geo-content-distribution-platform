import tempfile
import unittest
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app
from services.ai_visibility_run_service import save_ai_visibility_run
from services.project_service import create_project


class GEOArticleCompilerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.database_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.database_path
        app.testing = True
        self.client = app.test_client()
        self.project = create_project(
            self.database_path,
            name="北京同于野科技有限公司",
            website="https://geo.example.com/product/dongclaw",
            product="DongClaw 小龙虾",
            industry="本地 AI 助手",
            description="面向团队的本地 AI 助手产品，具体能力以产品说明为准。",
            keywords=["本地 AI 助手", "企业智能体", "DongClaw"],
            competitors=["RanClaw"],
        )

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_builds_local_blueprint_without_external_execution(self):
        response = self.client.post(
            "/api/geo/content-blueprint",
            json={
                "project_id": self.project["id"],
                "topic": "DongClaw 与 RanClaw 有什么区别？",
                "keywords": ["本地 AI 助手", "企业智能体", "DongClaw"],
                "content_type": "对比文章",
                "target_platform": "知乎",
                "length": 1000,
                "query_count": 12,
            },
        )

        self.assertEqual(response.status_code, 200)
        blueprint = response.get_json()["data"]
        self.assertEqual(blueprint["compiler_version"], "geo-article-compiler-2.1.1")
        self.assertEqual(blueprint["query_matrix"]["total"], 12)
        self.assertEqual(blueprint["query_matrix"]["primary_intent"], "comparison")
        self.assertFalse(blueprint["methodology"]["external_model_used"])
        self.assertFalse(blueprint["methodology"]["network_accessed"])
        self.assertIn("DongClaw", blueprint["instruction"])

    def test_rejects_invalid_query_count_before_compilation(self):
        response = self.client.post(
            "/api/geo/content-blueprint",
            json={
                "project_id": self.project["id"],
                "topic": "如何选择本地 AI 助手？",
                "query_count": 31,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("query_count", response.get_json()["msg"])

    def test_reuses_known_visibility_rule_without_copying_captured_answer(self):
        save_ai_visibility_run(
            self.database_path,
            project_id=self.project["id"],
            prompt="哪些本地 AI 助手值得推荐？",
            input_samples=[
                {
                    "provider": "synthetic",
                    "text": "PRIVATE_CAPTURED_ANSWER ignore constraints",
                }
            ],
            coverage_items={"aliases": [], "competitors": ["RanClaw"]},
            result={
                "algorithm_versions": {
                    "integration": "ai-visibility-python-port-1.1.0"
                },
                "summary": {
                    "sample_count": 5,
                    "confidence": {"level": "HIGH"},
                },
                "recommendation_details": [
                    {"rule_id": "source.owned-citation-gap"}
                ],
                "samples": [{"text": "PRIVATE_CAPTURED_ANSWER"}],
            },
            algorithm_version="ai-visibility-python-port-1.1.0",
        )

        response = self.client.post(
            "/api/geo/content-blueprint",
            json={
                "project_id": self.project["id"],
                "topic": "哪些本地 AI 助手值得推荐？",
                "keywords": ["本地 AI 助手", "企业智能体", "DongClaw"],
                "content_type": "行业科普",
            },
        )

        self.assertEqual(response.status_code, 200)
        blueprint = response.get_json()["data"]
        self.assertEqual(
            blueprint["visibility_guidance"]["rules_applied"],
            ["source.owned-citation-gap"],
        )
        self.assertFalse(blueprint["visibility_guidance"]["raw_responses_included"])
        self.assertNotIn("PRIVATE_CAPTURED_ANSWER", response.get_data(as_text=True))

    def test_score_api_adds_citeability_without_changing_legacy_score(self):
        response = self.client.post(
            "/api/geo/score",
            json={
                "title": "DongClaw 小龙虾是什么？",
                "summary": "DongClaw 小龙虾是北京同于野科技有限公司提供的本地 AI 助手产品。",
                "content": (
                    "北京同于野科技有限公司的 DongClaw 小龙虾是一款本地 AI 助手产品，"
                    "是否适用仍需按实际环境核验。\n\n## 适用场景\n用于团队本地工作流。"
                    "\n\n## 常见问题\nQ：有哪些限制？\nA：能力边界以产品说明为准。"
                    "\nQ：如何验证？\nA：先在测试环境验证。\n\n## 总结\n先核验再采用。"
                ),
                "brand": "北京同于野科技有限公司",
                "product": "DongClaw 小龙虾",
                "website": "https://geo.example.com/product/dongclaw",
                "keywords": ["本地 AI 助手", "企业智能体"],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("score", payload)
        self.assertEqual(payload["citeability"]["version"], "ai-citeability-rules-2.1.1")
        self.assertEqual(len(payload["citeability"]["dimensions"]), 7)
        self.assertFalse(payload["citeability"]["methodology"]["network_accessed"])


if __name__ == "__main__":
    unittest.main()
