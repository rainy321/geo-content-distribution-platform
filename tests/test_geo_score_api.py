import unittest
from unittest.mock import patch

from sau_backend import app


class GeoScoreApiTests(unittest.TestCase):
    def setUp(self):
        self.original_testing = app.testing
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.testing = self.original_testing

    @patch("sau_backend.score_geo_content")
    def test_scores_valid_request_and_normalizes_keywords(self, mock_score):
        mock_score.return_value = {
            "score": 82,
            "dimensions": {
                "entity": 90,
                "keywords": 85,
                "structure": 80,
                "faq": 70,
                "citation": 60,
            },
            "suggestions": ["建议增加权威数据来源"],
        }

        response = self.client.post(
            "/api/geo/score",
            json={
                "title": "企业 AI Agent 指南",
                "content": "正文",
                "brand": "XX科技",
                "keywords": [" AI Agent ", "AI Agent", "企业智能体"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["score"], 82)
        self.assertEqual(
            mock_score.call_args.kwargs,
            {
                "title": "企业 AI Agent 指南",
                "content": "正文",
                "brand": "XX科技",
                "keywords": ["AI Agent", "企业智能体"],
                "target_length": None,
                "requested_target_length": None,
                "source": None,
            },
        )

    def test_explicit_target_length_selects_a_short_form_profile(self):
        response = self.client.post(
            "/api/geo/score",
            json={
                "title": "XX科技 AI Agent 是什么？",
                "content": (
                    "XX科技 AI Agent 面向需要梳理业务流程的企业团队，"
                    "提供从需求确认到结果复核的内容辅助能力。"
                    "使用前应核对实际产品说明、适用条件与数据边界。\n\n"
                    "详细信息以企业公开资料为准：https://example.com/product"
                ),
                "brand": "XX科技",
                "product": "AI Agent",
                "website": "https://example.com",
                "keywords": ["AI Agent"],
                "target_length": 200,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["length_profile"]["key"], "compact")
        self.assertEqual(payload["length_profile"]["target_length"], 200)
        self.assertIsNone(payload["dimensions"]["faq"])
        self.assertNotIn(
            "建议将正文扩充至至少 600 字",
            payload["suggestions"],
        )
        self.assertEqual(
            payload["citeability"]["length_profile"]["target_length"],
            200,
        )
        self.assertIsNone(
            payload["citeability"]["dimensions"]["faq_decision_support"]
        )

    def test_rejects_invalid_target_lengths(self):
        for target_length in (True, 199, 200.5, 3001):
            with self.subTest(target_length=target_length):
                response = self.client.post(
                    "/api/geo/score",
                    json={
                        "title": "标题",
                        "content": "正文",
                        "brand": "XX科技",
                        "keywords": [],
                        "target_length": target_length,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("target_length", response.get_json()["msg"])

    def test_overlong_copy_cannot_keep_compact_scoring_exemptions(self):
        response = self.client.post(
            "/api/geo/score",
            json={
                "title": "XX科技 AI Agent 是什么？",
                "content": "XX科技介绍 AI Agent。" + "真实正文" * 260,
                "brand": "XX科技",
                "keywords": ["AI Agent"],
                "target_length": 200,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["length_profile"]["target_length"], 200)
        self.assertEqual(
            payload["length_profile"]["source"],
            "actual_length_upgrade",
        )
        self.assertTrue(payload["length_profile"]["over_target"])
        self.assertIsInstance(payload["dimensions"]["faq"], int)

    def test_rejects_empty_json_body(self):
        response = self.client.post("/api/geo/score", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["msg"], "文章标题不能为空")

    def test_rejects_missing_content_and_brand(self):
        missing_content = self.client.post(
            "/api/geo/score",
            json={"title": "标题", "brand": "XX科技"},
        )
        missing_brand = self.client.post(
            "/api/geo/score",
            json={"title": "标题", "content": "正文"},
        )

        self.assertEqual(missing_content.status_code, 400)
        self.assertEqual(missing_content.get_json()["msg"], "文章正文不能为空")
        self.assertEqual(missing_brand.status_code, 400)
        self.assertEqual(missing_brand.get_json()["msg"], "品牌名称不能为空")

    def test_rejects_non_list_keywords(self):
        response = self.client.post(
            "/api/geo/score",
            json={
                "title": "标题",
                "content": "正文",
                "brand": "XX科技",
                "keywords": "AI Agent",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("keywords", response.get_json()["msg"])


if __name__ == "__main__":
    unittest.main()
