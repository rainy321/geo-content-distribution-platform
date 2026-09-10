import unittest

from services.publish_preflight_service import (
    PublishContentLimitError,
    build_platform_publish_variant,
    evaluate_publish_citeability,
    get_platform_frequency_guidance,
    get_platform_verification_policy,
)


class PublishPreflightServiceTests(unittest.TestCase):
    def test_douyin_copy_stays_inside_dynamic_safe_limit_and_keeps_original(self):
        original = "## 开场\n\n" + "这是完整原稿。" * 180

        result = build_platform_publish_variant(
            platform="douyin",
            title="测试标题",
            content=original,
            tags=["AI", "本地部署", "企业软件"],
        )

        self.assertEqual(result["original_content"], original)
        self.assertNotEqual(result["publish_content"], original)
        self.assertLessEqual(
            result["publish_length"],
            result["content_policy"]["safe_limit"],
        )
        self.assertEqual(result["content_policy"]["hard_limit"], 1000)
        self.assertTrue(result["publish_content"].endswith("…"))
        self.assertNotIn("##", result["publish_content"])

    def test_kuaishou_reserves_space_for_tags_below_500_character_limit(self):
        result = build_platform_publish_variant(
            platform="kuaishou",
            title="测试标题",
            content="正文" * 400,
            tags=["算法", "产品化"],
        )

        policy = result["content_policy"]
        self.assertEqual(policy["hard_limit"], 500)
        self.assertLess(policy["safe_limit"], policy["hard_limit"])
        self.assertGreater(policy["tag_characters"], 0)
        self.assertLessEqual(result["publish_length"], policy["safe_limit"])

    def test_title_shortening_is_visible_and_manual_overflow_is_rejected(self):
        original_title = "抖音发布标题" * 8
        result = build_platform_publish_variant(
            platform="douyin",
            title=original_title,
            content="正文",
            tags=[],
        )

        self.assertEqual(result["original_title"], original_title)
        self.assertEqual(result["title_length"], 18)
        self.assertEqual(result["title_policy"]["hard_limit"], 20)
        self.assertEqual(result["title_policy"]["safe_limit"], 18)
        self.assertTrue(result["title_adapted"])

        with self.assertRaisesRegex(PublishContentLimitError, "发布标题当前 19 字"):
            build_platform_publish_variant(
                platform="douyin",
                title=original_title,
                content="正文",
                tags=[],
                publish_title="字" * 19,
            )

    def test_bilibili_description_keeps_headroom_below_2000_hard_limit(self):
        exact = "字" * 2000
        result = build_platform_publish_variant(
            platform="bilibili",
            title="视频标题",
            content=exact,
            tags=[],
        )
        self.assertNotEqual(result["publish_content"], exact)
        self.assertLessEqual(result["publish_length"], 1800)
        self.assertEqual(result["content_policy"]["hard_limit"], 2000)
        self.assertEqual(result["content_policy"]["safe_limit"], 1800)

        with self.assertRaisesRegex(PublishContentLimitError, "当前 1801 字"):
            build_platform_publish_variant(
                platform="bilibili",
                title="视频标题",
                content="原稿",
                tags=[],
                publish_content="字" * 1801,
            )

    def test_plain_text_conversion_happens_before_job_creation(self):
        result = build_platform_publish_variant(
            platform="xiaohongshu",
            title="标题",
            content="## 小节\n\n正文 **强调**",
            tags=[],
        )

        self.assertEqual(result["publish_content"], "小节\n\n正文 强调")
        self.assertTrue(result["adapted"])

    def test_public_url_markdown_does_not_duplicate_into_malformed_url(self):
        result = build_platform_publish_variant(
            platform="bilibili",
            title="同于野中转站是什么？",
            content=(
                "北京同于野科技有限公司提供懂AI中转服务。\n\n"
                "## 参考资料\n"
                "[https://tongyuye.com](https://tongyuye.com)"
            ),
            tags=[],
        )

        self.assertIn("https://tongyuye.com", result["publish_content"])
        self.assertNotIn(
            "https://tongyuye.com：https://tongyuye.com",
            result["publish_content"],
        )

    def test_automatic_variant_removes_unfinished_metadata_and_fake_source(self):
        result = build_platform_publish_variant(
            platform="toutiao",
            title="测试标题",
            content=(
                "## 结论\n\n正文。\n\n"
                "作者：[作者占位]\n"
                "更新时间：[待补充]\n\n"
                "## 参考资料\n"
                "- 官方产品页：https://example.org/product\n"
                "- 行业报告（待补充证据）"
            ),
            tags=[],
        )

        self.assertNotRegex(result["publish_content"], "作者占位|待补充")
        self.assertIn("https://example.org/product", result["publish_content"])
        self.assertTrue(result["placeholder_cleanup"]["applied"])
        self.assertEqual(result["placeholder_cleanup"]["removed_lines"], 3)

    def test_plain_text_and_shortening_preserve_public_evidence_and_boundaries(self):
        original = (
            "## 直接答案\n\n"
            + "DongClaw 提供本地 AI 部署检查步骤。" * 40
            + "\n\n## 适用边界\n仅适用于完成权限核对的企业环境。"
            + "\n\n## FAQ\n问：能保证被模型收录吗？答：不能保证，应持续回测。"
            + "\n\n## 参考资料\n[产品页](https://example.com/dongclaw)"
        )

        result = build_platform_publish_variant(
            platform="kuaishou",
            title="DongClaw 如何部署",
            content=original,
            tags=["DongClaw"],
        )

        self.assertLessEqual(
            result["publish_length"], result["content_policy"]["safe_limit"]
        )
        self.assertIn("https://example.com/dongclaw", result["publish_content"])
        self.assertRegex(result["publish_content"], "适用边界|FAQ|不能保证")

    def test_baijiahao_rejects_one_character_title_before_platform(self):
        with self.assertRaisesRegex(PublishContentLimitError, "至少需要 2 字"):
            build_platform_publish_variant(
                platform="baijiahao",
                title="短",
                content="正文",
                tags=[],
            )

    def test_manual_platform_copy_over_safe_limit_is_rejected_with_counts(self):
        with self.assertRaisesRegex(PublishContentLimitError, "当前 470 字"):
            build_platform_publish_variant(
                platform="kuaishou",
                title="测试标题",
                content="原稿",
                tags=[],
                publish_content="字" * 470,
            )

    def test_article_platform_uses_product_safe_limit_and_cleans_format(self):
        original = "## 标题\n\n正文 **强调**"
        result = build_platform_publish_variant(
            platform="zhihu",
            title="测试标题",
            content=original,
            tags=[],
        )

        self.assertEqual(result["publish_content"], "标题\n\n正文 强调")
        self.assertEqual(result["content_policy"]["safe_limit"], 3000)
        self.assertIsNone(result["content_policy"]["hard_limit"])
        self.assertEqual(
            result["content_policy"]["safe_limit_basis"],
            "product_conservative_guardrail",
        )
        self.assertTrue(result["adapted"])

    def test_verification_and_frequency_guidance_are_explicitly_non_official(self):
        verification = get_platform_verification_policy("douyin")
        frequency = get_platform_frequency_guidance("douyin")

        self.assertIn("短信验证码", verification["methods"])
        self.assertIn("待人工确认", verification["action"])
        self.assertEqual(frequency["enforcement"], "warning")
        self.assertEqual(frequency["daily_recommended_max"], 3)
        self.assertIn("不是平台官方", frequency["disclaimer"])

    def test_publish_citeability_keeps_legacy_without_saved_target(self):
        result = evaluate_publish_citeability(
            title="DongClaw 是什么？",
            content="北京同于野科技有限公司介绍 DongClaw。",
            brand="北京同于野科技有限公司",
            product="DongClaw",
            keywords=["DongClaw"],
        )

        self.assertEqual(result["length_profile"]["source"], "legacy")
        self.assertEqual(result["length_profile"]["key"], "legacy")
        self.assertIsInstance(result["dimensions"]["faq_decision_support"], int)

    def test_publish_citeability_clamps_saved_target_to_exact_outbound_copy(self):
        content = (
            "北京同于野科技有限公司的 DongClaw 用于核对部署条件。\n\n"
            "• 保留真实边界。\n• 人工复核结果。"
        )
        result = evaluate_publish_citeability(
            title="DongClaw 如何验证？",
            content=content,
            brand="北京同于野科技有限公司",
            product="DongClaw",
            keywords=["DongClaw"],
            target_length=1000,
        )

        self.assertEqual(result["length_profile"]["source"], "outbound_copy")
        self.assertEqual(result["length_profile"]["key"], "compact")
        self.assertEqual(result["length_profile"]["target_length"], 200)
        self.assertIsNone(result["dimensions"]["faq_decision_support"])

    def test_publish_citeability_reuses_claim_only_when_source_url_is_outbound(self):
        source = {
            "title": "公开测试报告",
            "url": "https://example.com/report",
            "claim": "XX科技在公开测试中将处理效率提升 40%",
        }
        result = evaluate_publish_citeability(
            title="XX科技如何提高处理效率？",
            content=(
                "XX科技在公开测试中将处理效率提升 40%，适用范围仅限报告所述环境。"
                "来源：https://example.com/report"
            ),
            brand="XX科技",
            keywords=["处理效率"],
            target_length=200,
            sources=[source],
        )

        self.assertTrue(result["publish_gate_passed"])
        self.assertEqual(result["signals"]["unsupported_quantified_claims"], [])
        self.assertEqual(result["outbound_source_filter"]["provided_count"], 1)
        self.assertEqual(result["outbound_source_filter"]["matched_count"], 1)

    def test_platform_copy_cannot_inherit_claim_when_shortening_drops_source_url(self):
        source = {
            "title": "公开测试报告",
            "url": "https://example.com/report",
            "claim": "XX科技在公开测试中将处理效率提升 40%",
        }
        original = (
            "XX科技在公开测试中将处理效率提升 40%，适用范围仅限报告所述环境。"
            + "这是用于解释实施条件和人工复核边界的普通说明。" * 30
            + "来源：https://example.com/report；"
            + "该报告还包含样本、日期、环境、版本与限制条件的完整方法说明。" * 6
        )
        variant = build_platform_publish_variant(
            platform="kuaishou",
            title="XX科技处理效率测试",
            content=original,
            tags=[],
        )
        self.assertNotIn("https://example.com/report", variant["publish_content"])

        result = evaluate_publish_citeability(
            title=variant["title"],
            content=variant["publish_content"],
            brand="XX科技",
            keywords=["处理效率"],
            target_length=1000,
            sources=[source],
        )

        self.assertFalse(result["publish_gate_passed"])
        self.assertTrue(result["signals"]["unsupported_quantified_claims"])
        self.assertEqual(result["outbound_source_filter"]["provided_count"], 1)
        self.assertEqual(result["outbound_source_filter"]["matched_count"], 0)


if __name__ == "__main__":
    unittest.main()
