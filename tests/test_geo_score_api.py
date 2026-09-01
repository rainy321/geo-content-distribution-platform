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
            },
        )

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
