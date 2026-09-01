import unittest

from uploader.toutiao_uploader.main import TouTiaoArticle


class ToutiaoArticleTopicMatchingTests(unittest.TestCase):
    def setUp(self):
        self.publisher = TouTiaoArticle(
            title="测试标题",
            body="测试正文",
            tags=[],
            publish_date=0,
            account_file="unused.json",
        )

    def test_accepts_exact_topic_candidate(self):
        self.assertTrue(
            self.publisher._is_real_topic_dropdown_item(
                "#内容分发# 13讨论",
                "内容分发",
            )
        )

    def test_accepts_exact_topic_when_spacing_differs(self):
        self.assertTrue(
            self.publisher._is_real_topic_dropdown_item(
                "#AI内容# 20讨论",
                "AI 内容",
            )
        )

    def test_rejects_candidate_that_only_contains_requested_topic(self):
        self.assertFalse(
            self.publisher._is_real_topic_dropdown_item(
                "#心脏功能测试# 16讨论",
                "功能测试",
            )
        )
        self.assertFalse(
            self.publisher._is_real_topic_dropdown_item(
                "#今日头条微头条# 36万讨论",
                "今日头条",
            )
        )

    def test_ranking_drops_non_exact_topics(self):
        candidates = [
            {"text": "#心脏功能测试# 16讨论", "score": 99},
            {"text": "#功能测试# 3讨论", "score": 10},
        ]

        ranked = self.publisher._rank_topic_candidates(candidates, "功能测试")

        self.assertEqual(["#功能测试# 3讨论"], [item["text"] for item in ranked])


if __name__ == "__main__":
    unittest.main()
