import unittest

from services.platform_capability_service import SUPPORTED_PUBLISH_PLATFORMS
from services.platform_publish_policy_service import (
    adapt_tags_for_platform,
    evaluate_platform_offline_preflight,
    get_platform_publish_policy,
    list_platform_publish_policies,
)
from services.publish_preflight_service import (
    PublishContentLimitError,
    build_platform_publish_variant,
    get_publish_content_policy,
    validate_platform_publish_variant,
)


class PlatformPublishPolicyServiceTests(unittest.TestCase):
    def test_matrix_has_one_independent_policy_for_all_ten_platforms(self):
        policies = list_platform_publish_policies()

        self.assertEqual(len(policies), 10)
        self.assertEqual(
            {item["platform"] for item in policies},
            set(SUPPORTED_PUBLISH_PLATFORMS),
        )
        for policy in policies:
            self.assertIn("title", policy)
            self.assertIn("content", policy)
            self.assertIn("tags", policy)
            self.assertIn("media", policy)
            self.assertFalse(policy["review"]["guaranteed_pass"])
            self.assertIn("不保证审核通过", policy["review"]["disclaimer"])

    def test_unknown_limits_remain_unknown_instead_of_becoming_fake_quotas(self):
        zhihu = get_platform_publish_policy("知乎")
        channels = get_platform_publish_policy("视频号")

        self.assertIsNone(zhihu["content"]["hard_limit"])
        self.assertEqual(zhihu["content"]["basis"], "not_verified")
        self.assertIsNone(channels["title"]["hard_limit"])
        self.assertEqual(channels["title"]["basis"], "not_verified")

    def test_media_constraints_come_from_capability_registry(self):
        xiaohongshu = get_platform_publish_policy("xiaohongshu")
        bilibili = get_platform_publish_policy("b站")

        self.assertTrue(xiaohongshu["media"]["requires_images"])
        self.assertFalse(xiaohongshu["media"]["requires_video"])
        self.assertTrue(bilibili["media"]["requires_video"])
        self.assertFalse(bilibili["media"]["requires_images"])

    def test_xiaohongshu_screenshot_limit_builds_a_safe_short_copy(self):
        original = "## 直接答案\n\n" + "这是企业大模型接口的中性说明。" * 100

        result = build_platform_publish_variant(
            platform="xiaohongshu",
            title="企业大模型接口怎么选",
            content=original,
            tags=["人工智能", "开发者"],
        )

        self.assertEqual(result["content_policy"]["hard_limit"], 1000)
        self.assertEqual(
            result["content_policy"]["basis"],
            "user_editor_screenshot_2026_09_10",
        )
        self.assertLessEqual(
            result["publish_length"], result["content_policy"]["safe_limit"]
        )
        self.assertNotIn("##", result["publish_content"])
        self.assertTrue(result["offline_preflight"]["passed"])

    def test_every_platform_copy_removes_literal_markdown_before_publish(self):
        content = (
            "## 直接答案\n\n"
            "**企业说明**\n\n"
            "---\n\n"
            "参考资料：[产品页](https://example.com/product)"
        )

        for platform in SUPPORTED_PUBLISH_PLATFORMS:
            with self.subTest(platform=platform):
                result = build_platform_publish_variant(
                    platform=platform,
                    title="企业产品使用说明",
                    content=content,
                    tags=[],
                )
                self.assertNotIn("##", result["publish_content"])
                self.assertNotIn("**", result["publish_content"])
                self.assertNotRegex(result["publish_content"], r"(?m)^---$")
                self.assertIn("https://example.com/product", result["publish_content"])

    def test_tag_preview_mirrors_only_repository_adapter_caps(self):
        tags = ["#核心", "行业", "核心", "产品", "案例", "指南", "更多"]

        self.assertEqual(
            adapt_tags_for_platform("toutiao", tags)["selected"], ["核心"]
        )
        self.assertEqual(len(adapt_tags_for_platform("kuaishou", tags)["selected"]), 4)
        self.assertEqual(len(adapt_tags_for_platform("baijiahao", tags)["selected"]), 5)
        self.assertEqual(len(adapt_tags_for_platform("sohu", tags)["selected"]), 6)
        self.assertEqual(adapt_tags_for_platform("zhihu", tags)["selected"], [])

    def test_tag_reserve_uses_only_tags_the_adapter_can_submit(self):
        many_tags = [f"标签{index}" for index in range(10)]

        kuaishou = get_publish_content_policy("kuaishou", tags=many_tags)
        bilibili = get_publish_content_policy("bilibili", tags=many_tags)

        self.assertEqual(kuaishou["tag_count_used"], 4)
        self.assertEqual(kuaishou["tag_count_dropped"], 6)
        self.assertTrue(kuaishou["tags_share_content_limit"])
        self.assertEqual(bilibili["safe_limit"], 2000)
        self.assertEqual(bilibili["tag_characters"], 0)
        self.assertFalse(bilibili["tags_share_content_limit"])

    def test_deterministic_placeholders_and_review_guarantees_are_blockers(self):
        result = evaluate_platform_offline_preflight(
            platform="sohu",
            title="企业模型接口使用说明",
            content=(
                "第一部分介绍产品。\n\n"
                "作者：[待补充]\n\n"
                "本工具保证审核通过。\n\n"
                "来源：https://example.com/product"
            ),
            tags=[],
        )

        self.assertFalse(result["passed"])
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("unfinished_placeholder", codes)
        self.assertIn("approval_guarantee_claim", codes)

        with self.assertRaisesRegex(PublishContentLimitError, "离线预检未通过"):
            validate_platform_publish_variant(
                platform="sohu",
                title="企业模型接口使用说明",
                content=(
                    "第一部分介绍产品。\n\n"
                    "作者：[待补充]\n\n"
                    "本工具保证审核通过。\n\n"
                    "来源：https://example.com/product"
                ),
                tags=[],
            )

    def test_honest_non_guarantee_boundary_is_not_misclassified_as_a_claim(self):
        result = evaluate_platform_offline_preflight(
            platform="sohu",
            title="企业内容发布边界说明",
            content=(
                "本文说明企业内容发布的本地预检机制。\n\n"
                "软件不能保证审核通过，也不能保证被模型收录。\n\n"
                "参考资料：https://example.com/policy"
            ),
            tags=[],
        )

        codes = {item["code"] for item in result["issues"]}
        self.assertNotIn("approval_guarantee_claim", codes)
        self.assertNotIn("absolute_claim_needs_evidence", codes)
        self.assertTrue(result["passed"])

    def test_manual_markdown_copy_is_rejected_before_adapter_execution(self):
        with self.assertRaisesRegex(PublishContentLimitError, "Markdown"):
            validate_platform_publish_variant(
                platform="bilibili",
                title="企业模型接口使用说明",
                content=(
                    "## 直接答案\n\n"
                    "**企业说明**\n\n"
                    "参考资料：[产品页](https://example.com/product)"
                ),
                tags=[],
            )

    def test_sohu_returns_explainable_conservative_article_warnings(self):
        result = build_platform_publish_variant(
            platform="sohu",
            title="企业模型接口使用说明",
            content=(
                "## 直接答案\n\n"
                "这是一项面向开发者的模型接口服务。\n\n"
                "立即购买并私信领取优惠。"
            ),
            tags=["AI"],
        )

        self.assertEqual(
            result["platform_policy"]["format"],
            "conservative_enterprise_education_article",
        )
        self.assertNotIn("##", result["publish_content"])
        codes = {item["code"] for item in result["offline_preflight"]["issues"]}
        self.assertIn("sohu_promotional_call_to_action", codes)
        self.assertIn("sohu_no_public_source_url", codes)
        self.assertTrue(result["offline_preflight"]["passed"])


if __name__ == "__main__":
    unittest.main()
