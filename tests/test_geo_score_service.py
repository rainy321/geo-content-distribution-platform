import unittest

from services.geo_score_service import score_geo_content


class GeoScoreServiceTests(unittest.TestCase):
    def test_complete_article_scores_one_hundred(self):
        long_paragraph = "企业可根据实际业务目标规划实施步骤，并持续核验内容事实。" * 14
        content = f"""XX科技专注企业 AI Agent 产品与服务。2026 年已形成 3 项实践方法。

## 落地方法
{long_paragraph}

### 实施建议
{long_paragraph}

## FAQ
问：企业智能体适合哪些团队？
答：适合需要改善服务流程的企业团队。

## 总结
企业应结合自身情况稳步实施。数据来源：https://example.test/report
"""

        result = score_geo_content(
            title="企业 AI Agent 落地指南",
            content=content,
            brand="XX科技",
            keywords=["AI Agent", "企业智能体"],
        )

        self.assertEqual(result["score"], 100)
        self.assertEqual(
            result["dimensions"],
            {
                "entity": 100,
                "keywords": 100,
                "structure": 100,
                "faq": 100,
                "citation": 100,
            },
        )
        self.assertEqual(result["suggestions"], [])

    def test_sparse_article_returns_low_score_and_actionable_suggestions(self):
        result = score_geo_content(
            title="普通标题",
            content="这是一篇很短的正文。",
            brand="XX科技",
            keywords=["AI Agent"],
        )

        self.assertEqual(result["score"], 5)
        self.assertEqual(result["dimensions"]["entity"], 0)
        self.assertEqual(result["dimensions"]["keywords"], 17)
        self.assertIn(
            "建议自然补充未覆盖的核心关键词：AI Agent",
            result["suggestions"],
        )
        self.assertIn("建议增加权威数据来源或外部链接", result["suggestions"])

    def test_keyword_coverage_is_proportional_and_deduplicated(self):
        result = score_geo_content(
            title="AI Agent 实践",
            content="XX科技介绍 AI Agent 的实际使用方式。",
            brand="XX科技",
            keywords=["AI Agent", "ai agent", "企业智能体"],
        )

        self.assertEqual(result["score"], 38)
        self.assertIn(
            "建议自然补充未覆盖的核心关键词：企业智能体",
            result["suggestions"],
        )

    def test_detects_obvious_keyword_stuffing(self):
        result = score_geo_content(
            title="AI Agent 介绍",
            content="XX科技：AI Agent AI Agent AI Agent。",
            brand="XX科技",
            keywords=["AI Agent"],
        )

        self.assertEqual(result["dimensions"]["keywords"], 83)
        self.assertIn(
            "核心关键词出现过于密集，建议降低重复频率",
            result["suggestions"],
        )

    def test_recognizes_html_headings_and_question_markers(self):
        content = """<h2>实施步骤</h2>

第一段。

第二段。

Q：问题一
A：答案一
Q：问题二
A：答案二
"""
        result = score_geo_content(
            title="测试文章",
            content=content,
            brand="测试品牌",
            keywords=[],
        )

        self.assertEqual(result["dimensions"]["faq"], 100)
        self.assertNotIn("建议增加清晰的 H2/H3 小标题", result["suggestions"])


if __name__ == "__main__":
    unittest.main()
