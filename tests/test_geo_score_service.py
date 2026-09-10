import unittest

from services.geo_score_service import GEO_SCORE_VERSION, score_geo_content


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
企业应结合自身情况稳步实施。数据来源：https://example.com/report
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

    def test_short_copy_detects_punctuation_case_and_zero_width_stuffing(self):
        result = score_geo_content(
            title="AI Agent 介绍",
            content="XX科技：Ai Agent，\nAI\u200b Agent；ai-agent。",
            brand="XX科技",
            keywords=["AI Agent"],
            target_length=200,
        )

        self.assertEqual(result["score_details"]["no_stuffing"]["earned_points"], 0)
        self.assertIn(
            "核心关键词出现过于密集，建议降低重复频率",
            result["suggestions"],
        )

    def test_nested_company_and_product_keywords_do_not_double_count_density(self):
        result = score_geo_content(
            title="同于野 AI 中转站适合哪些场景？",
            content=(
                "北京同于野科技有限公司提供同于野 AI 中转站的公开产品说明，"
                "团队应先核对接口兼容性、计费规则、数据边界和实际服务条款。\n\n"
                "选择前可围绕一个真实调用流程做验证，记录模型、时间、输入输出与异常，"
                "再根据可复核结果判断是否适用，不把未经验证的效果写成事实。"
            ),
            brand="北京同于野科技有限公司",
            keywords=[
                "同于野",
                "同于野科技",
                "北京同于野科技有限公司",
                "同于野 AI 中转站",
            ],
            target_length=200,
        )

        self.assertEqual(result["score_details"]["no_stuffing"]["earned_points"], 5)
        self.assertFalse(any("关键词出现过于密集" in item for item in result["suggestions"]))

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

    def test_200_character_profile_excludes_long_form_checks_without_free_points(self):
        content = (
            "XX科技的 AI Agent 面向需要梳理业务流程的团队，使用前应先核对数据边界、"
            "责任范围和实际部署条件。2026 年的具体能力应以可验证资料为准。\n\n"
            "团队可以先选择一个真实场景进行小范围验证，再记录问题与结果，"
            "资料来源：https://example.com/agent"
        )

        result = score_geo_content(
            title="AI Agent 适合哪些团队？",
            content=content,
            brand="XX科技",
            keywords=["AI Agent"],
            target_length=200,
        )

        self.assertEqual(result["version"], GEO_SCORE_VERSION)
        self.assertEqual(result["score"], 100)
        self.assertIsNone(result["dimensions"]["faq"])
        self.assertEqual(result["length_profile"]["key"], "compact")
        self.assertEqual(result["length_profile"]["source"], "explicit")
        self.assertEqual(result["length_profile"]["not_applicable_dimensions"], ["faq"])
        for key in ("headings", "faq", "summary"):
            self.assertFalse(result["score_details"][key]["applicable"])
            self.assertEqual(result["score_details"][key]["earned_points"], 0)
        self.assertFalse(any("H2/H3" in item for item in result["suggestions"]))
        self.assertFalse(any("常见问题" in item for item in result["suggestions"]))
        self.assertFalse(any("总结" in item for item in result["suggestions"]))

    def test_visible_length_profile_ignores_whitespace_but_keeps_markdown_markers(self):
        content = "## 标题\n\n" + ("字 " * 140)

        result = score_geo_content(
            title="短稿",
            content=content,
            brand="品牌",
            keywords=[],
            target_length=200,
        )

        self.assertGreater(len(content), 200)
        self.assertEqual(result["length_profile"]["actual_length"], 144)

    def test_target_length_profiles_cover_presets_and_custom_values(self):
        cases = (
            (200, "compact", "200", 120, 2, False, 2),
            (600, "short", "600", 360, 2, False, 3),
            (1000, "standard", "1000", 600, 3, True, 5),
            (1500, "long", "1500", 900, 4, True, 7),
            (750, "short", "custom", 450, 2, False, 3),
            (1300, "long", "custom", 780, 4, True, 7),
        )
        keywords = [f"关键词{index}" for index in range(1, 8)]
        for (
            target,
            key,
            preset,
            minimum,
            paragraphs,
            faq_required,
            evaluated_count,
        ) in cases:
            with self.subTest(target=target):
                result = score_geo_content(
                    title="AI Agent 说明",
                    content="XX科技介绍 AI Agent。",
                    brand="XX科技",
                    keywords=keywords,
                    target_length=target,
                )
                profile = result["length_profile"]
                self.assertEqual(profile["key"], key)
                self.assertEqual(profile["target_length"], target)
                self.assertEqual(profile["minimum_length"], minimum)
                self.assertEqual(profile["criteria"]["preset"], preset)
                self.assertEqual(profile["criteria"]["minimum_paragraphs"], paragraphs)
                self.assertEqual(profile["criteria"]["faq_required"], faq_required)
                self.assertEqual(
                    len(profile["criteria"]["evaluated_keywords"]),
                    evaluated_count,
                )
                self.assertEqual(
                    profile["criteria"]["deferred_keyword_count"],
                    len(keywords) - evaluated_count,
                )

    def test_platform_effective_target_preserves_original_request_metadata(self):
        result = score_geo_content(
            title="XX科技 AI Agent 是什么？",
            content=(
                "XX科技的 AI Agent 用于验证企业流程边界。\n\n"
                "团队应先核对公开资料和实际条件，再决定是否使用。"
            ),
            brand="XX科技",
            keywords=["AI Agent", "企业流程", "公开资料"],
            target_length=380,
            requested_target_length=1000,
            source="platform_safe_limit",
        )

        profile = result["length_profile"]
        self.assertEqual(profile["key"], "compact")
        self.assertEqual(profile["target_length"], 1000)
        self.assertEqual(profile["requested_target_length"], 1000)
        self.assertEqual(profile["effective_target_length"], 380)
        self.assertEqual(profile["criteria"]["scoring_target_length"], 380)
        self.assertEqual(profile["source"], "platform_safe_limit")

    def test_missing_target_preserves_legacy_long_form_contract(self):
        result = score_geo_content(
            title="AI Agent 是什么？",
            content="XX科技介绍 AI Agent 的实际用途。",
            brand="XX科技",
            keywords=["AI Agent"],
        )

        profile = result["length_profile"]
        self.assertEqual(profile["source"], "legacy")
        self.assertEqual(profile["key"], "legacy")
        self.assertIsNone(profile["target_length"])
        self.assertIsInstance(result["dimensions"]["faq"], int)

    def test_short_target_still_requires_actual_content_and_evidence(self):
        result = score_geo_content(
            title="AI Agent",
            content="XX科技介绍 AI Agent。",
            brand="XX科技",
            keywords=["AI Agent"],
            target_length=200,
        )

        self.assertEqual(result["score_details"]["length"]["earned_points"], 0)
        self.assertEqual(result["score_details"]["facts"]["earned_points"], 0)
        self.assertEqual(result["score_details"]["source"]["earned_points"], 0)
        self.assertLess(result["score"], 100)
        self.assertIn(
            "只按实际通过的适用检查计分",
            "".join(result["length_profile"]["guidance"]),
        )

    def test_bare_digit_and_empty_source_never_earn_evidence_points_in_any_length_band(self):
        for target_length, expected_band in (
            (200, "compact"),
            (600, "short"),
            (1500, "long"),
        ):
            with self.subTest(target_length=target_length):
                result = score_geo_content(
                    title="测试品牌核心能力",
                    content=(
                        "测试品牌围绕核心能力说明第 1 步，但这里没有统计口径、"
                        "单位或可复核事实。\n\n来源："
                    ),
                    brand="测试品牌",
                    keywords=["核心能力"],
                    target_length=target_length,
                )

                self.assertEqual(result["length_profile"]["key"], expected_band)
                self.assertEqual(result["score_details"]["facts"]["earned_points"], 0)
                self.assertEqual(result["score_details"]["source"]["earned_points"], 0)
                self.assertEqual(result["dimensions"]["citation"], 0)

    def test_explicit_fact_and_public_url_earn_evidence_points_in_any_length_band(self):
        for target_length, expected_band in (
            (200, "compact"),
            (600, "short"),
            (1500, "long"),
        ):
            with self.subTest(target_length=target_length):
                result = score_geo_content(
                    title="测试品牌核心能力",
                    content=(
                        "测试品牌公开测试覆盖 30 家样本企业，并明确记录适用环境。\n\n"
                        "数据来源：https://example.com/public-report"
                    ),
                    brand="测试品牌",
                    keywords=["核心能力"],
                    target_length=target_length,
                )

                self.assertEqual(result["length_profile"]["key"], expected_band)
                self.assertEqual(result["score_details"]["facts"]["earned_points"], 5)
                self.assertEqual(result["score_details"]["source"]["earned_points"], 5)
                self.assertEqual(result["dimensions"]["citation"], 100)

    def test_non_public_or_credentialed_urls_do_not_count_as_sources(self):
        for source in (
            "http://127.0.0.1/report",
            "http://169.254.169.254/latest",
            "https://internal.local/report",
            "https://user:secret@example.com/report",
            "https://placeholder.example.test/report",
        ):
            with self.subTest(source=source):
                result = score_geo_content(
                    title="测试品牌核心能力",
                    content=f"测试品牌记录了 30 项结果。\n\n来源：{source}",
                    brand="测试品牌",
                    keywords=["核心能力"],
                    target_length=600,
                )

                self.assertEqual(result["score_details"]["facts"]["earned_points"], 5)
                self.assertEqual(result["score_details"]["source"]["earned_points"], 0)
                self.assertEqual(result["dimensions"]["citation"], 50)

    def test_complete_formal_reference_or_doi_counts_without_a_placeholder_label(self):
        for reference in (
            "中国信息通信研究院《人工智能发展报告》2026 年",
            "doi: 10.1145/1234567.1234568",
        ):
            with self.subTest(reference=reference):
                result = score_geo_content(
                    title="测试品牌核心能力",
                    content=(
                        "测试品牌提供公开能力说明，并明确适用条件。\n\n"
                        f"参考资料：{reference}"
                    ),
                    brand="测试品牌",
                    keywords=["核心能力"],
                    target_length=600,
                )

                self.assertEqual(result["score_details"]["source"]["earned_points"], 5)
                self.assertEqual(result["score_details"]["facts"]["earned_points"], 0)

    def test_rejects_invalid_target_length(self):
        for value in (True, "200", 199, 3001):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    score_geo_content(
                        title="标题",
                        content="正文",
                        brand="品牌",
                        keywords=[],
                        target_length=value,
                    )

    def test_compact_profile_evaluates_only_priority_keywords_but_checks_stuffing_globally(self):
        keywords = [
            "核心一",
            "核心二",
            "扩展三",
            "扩展四",
            "扩展五",
            "扩展六",
            "扩展七",
        ]
        result = score_geo_content(
            title="扩展七使用说明",
            content=(
                "测试品牌围绕核心一和核心二说明真实适用条件。\n\n"
                "扩展七 扩展七 扩展七。"
            ),
            brand="测试品牌",
            keywords=keywords,
            target_length=200,
        )

        criteria = result["length_profile"]["criteria"]
        self.assertEqual(criteria["evaluated_keywords"], ["核心一", "核心二"])
        self.assertEqual(criteria["deferred_keyword_count"], 5)
        self.assertEqual(result["score_details"]["keyword_coverage"]["earned_points"], 15)
        self.assertEqual(result["score_details"]["title_keyword"]["earned_points"], 10)
        self.assertEqual(result["score_details"]["no_stuffing"]["earned_points"], 0)
        self.assertFalse(any("扩展三" in item for item in result["suggestions"]))

    def test_actual_length_upgrades_a_falsely_declared_compact_profile(self):
        content = "测试品牌介绍核心关键词的真实适用条件。" + "正文内容" * 260
        result = score_geo_content(
            title="核心关键词是什么？",
            content=content,
            brand="测试品牌",
            keywords=["核心关键词"],
            target_length=200,
        )

        profile = result["length_profile"]
        self.assertEqual(profile["target_length"], 200)
        self.assertGreaterEqual(profile["effective_target_length"], 1000)
        self.assertEqual(profile["source"], "actual_length_upgrade")
        self.assertTrue(profile["over_target"])
        self.assertIn(profile["key"], {"standard", "long"})
        self.assertIsInstance(result["dimensions"]["faq"], int)


if __name__ == "__main__":
    unittest.main()
