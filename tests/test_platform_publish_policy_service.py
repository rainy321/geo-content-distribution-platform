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
    get_publish_title_policy,
    validate_platform_publish_variant,
)


class PlatformPublishPolicyServiceTests(unittest.TestCase):
    def test_matrix_has_one_independent_policy_for_all_eleven_platforms(self):
        policies = list_platform_publish_policies()

        self.assertEqual(len(policies), 11)
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

    def test_unknown_hard_limits_use_explicit_product_safe_lines(self):
        zhihu = get_platform_publish_policy("知乎")
        channels = get_platform_publish_policy("视频号")

        self.assertIsNone(zhihu["content"]["hard_limit"])
        self.assertEqual(zhihu["content"]["basis"], "not_verified")
        self.assertEqual(zhihu["content"]["safe_limit"], 3000)
        self.assertEqual(
            zhihu["content"]["safe_limit_basis"],
            "product_conservative_guardrail",
        )
        self.assertIsNone(channels["title"]["hard_limit"])
        self.assertEqual(channels["title"]["basis"], "not_verified")
        self.assertEqual(channels["title"]["safe_limit"], 30)
        self.assertEqual(
            channels["title"]["safe_limit_basis"],
            "product_conservative_guardrail",
        )

    def test_all_eleven_platforms_have_title_and_content_safe_limits(self):
        for platform in SUPPORTED_PUBLISH_PLATFORMS:
            with self.subTest(platform=platform):
                canonical = get_platform_publish_policy(platform)
                title_policy = get_publish_title_policy(platform)
                content_policy = get_publish_content_policy(platform, tags=[])

                self.assertIsNotNone(title_policy)
                self.assertIsNotNone(content_policy)
                self.assertGreater(title_policy["safe_limit"], 0)
                self.assertGreater(content_policy["safe_limit"], 0)
                self.assertEqual(
                    title_policy["safe_limit"],
                    canonical["title"]["safe_limit"],
                )
                self.assertEqual(
                    content_policy["safe_limit"],
                    canonical["content"]["safe_limit"],
                )
                for field, effective in (
                    ("title", title_policy),
                    ("content", content_policy),
                ):
                    if canonical[field]["hard_limit"] is None:
                        self.assertEqual(
                            effective["safe_limit_basis"],
                            "product_conservative_guardrail",
                        )

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
        self.assertEqual(adapt_tags_for_platform("baijiahao", tags)["selected"], [])
        self.assertEqual(len(adapt_tags_for_platform("xiaohongshu", tags)["selected"]), 5)
        self.assertEqual(len(adapt_tags_for_platform("douyin", tags)["selected"]), 5)
        sohu = adapt_tags_for_platform("sohu", tags)
        self.assertEqual(sohu["selected"], ["核心", "行业", "产品"])
        self.assertEqual(sohu["policy"]["basis"], "product_conservative_guardrail")
        self.assertIn("不是搜狐号官方配额", sohu["policy"]["disclaimer"])
        tiktok = adapt_tags_for_platform("tiktok", tags)
        self.assertEqual(len(tiktok["selected"]), 5)
        self.assertEqual(
            tiktok["policy"]["basis"],
            "product_operational_guardrail",
        )
        self.assertEqual(adapt_tags_for_platform("zhihu", tags)["selected"], [])

    def test_weibo_preflight_and_execution_share_one_total_text_budget(self):
        title = "标" * 80
        content = "文" * 620
        tags = [f"标签{index}" + "长" * 13 for index in range(5)]

        variant = validate_platform_publish_variant(
            platform="weibo",
            title=title,
            content=content,
            tags=tags,
        )

        self.assertLessEqual(variant["submission_preview"]["text_length"], 800)
        self.assertEqual(variant["submission_preview"]["safe_limit"], 800)
        self.assertEqual(
            adapt_tags_for_platform("weibo", tags)["selected"],
            [tag[:16] for tag in tags],
        )
        with self.assertRaisesRegex(PublishContentLimitError, "621 字"):
            validate_platform_publish_variant(
                platform="weibo",
                title=title,
                content="文" * 621,
                tags=tags,
            )

    def test_eight_tags_are_bounded_for_douyin_tiktok_and_bilibili(self):
        tags = [f"标签{index}" for index in range(1, 9)]

        self.assertEqual(
            adapt_tags_for_platform("douyin", tags)["selected"],
            tags[:5],
        )
        self.assertEqual(
            adapt_tags_for_platform("tiktok", tags)["selected"],
            tags[:5],
        )
        bilibili = adapt_tags_for_platform("bilibili", tags)
        self.assertEqual(bilibili["selected"], tags[:6])
        self.assertEqual(bilibili["dropped_count"], 2)
        self.assertEqual(bilibili["policy"]["max_count"], 6)
        self.assertEqual(
            bilibili["policy"]["basis"],
            "product_conservative_guardrail",
        )

    def test_bilibili_long_tag_is_safely_truncated_with_audit(self):
        long_tag = "超长标签" * 6

        preview = adapt_tags_for_platform("bilibili", [long_tag, "短标签"])

        self.assertEqual(preview["selected"], [long_tag[:20], "短标签"])
        self.assertEqual(preview["dropped_count"], 0)
        self.assertEqual(preview["truncated_count"], 1)
        self.assertEqual(
            preview["truncated_tags"],
            [
                {
                    "original": long_tag,
                    "selected": long_tag[:20],
                    "max_length": 20,
                }
            ],
        )
        self.assertEqual(preview["audit"]["truncated_count"], 1)
        self.assertEqual(preview["policy"]["max_length"], 20)

        preflight = evaluate_platform_offline_preflight(
            platform="bilibili",
            title="视频标题",
            content="视频简介",
            tags=[long_tag],
        )
        issue_codes = {item["code"] for item in preflight["issues"]}
        self.assertIn("tags_truncated_for_adapter", issue_codes)

    def test_tag_reserve_uses_only_tags_the_adapter_can_submit(self):
        many_tags = [f"标签{index}" for index in range(10)]

        kuaishou = get_publish_content_policy("kuaishou", tags=many_tags)
        bilibili = get_publish_content_policy("bilibili", tags=many_tags)

        self.assertEqual(kuaishou["tag_count_used"], 4)
        self.assertEqual(kuaishou["tag_count_dropped"], 6)
        self.assertTrue(kuaishou["tags_share_content_limit"])
        self.assertEqual(bilibili["safe_limit"], 1800)
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

    def test_sohu_automatic_copy_removes_deterministic_diversion_residue(self):
        original = (
            "## 直接答案\n\n"
            "这是一项面向开发者的模型接口服务。\n\n"
            "文章说明接口兼容机制和适用边界。\n\n"
            "结果应以实际环境测试为准。\n\n"
            "结果应以实际环境测试为准。\n\n"
            "立即购买并私信领取优惠。\n\n"
            "官网：https://example.com/product\n\n"
            "#人工智能 #开发者"
        )
        result = build_platform_publish_variant(
            platform="sohu",
            title="企业模型接口使用说明",
            content=original,
            tags=["AI", "接口", "开发者", "第四个标签"],
        )

        self.assertEqual(
            result["platform_policy"]["format"],
            "conservative_enterprise_education_article",
        )
        self.assertEqual(result["original_content"], original)
        self.assertNotIn("##", result["publish_content"])
        self.assertNotIn("立即购买", result["publish_content"])
        self.assertNotIn("https://example.com/product", result["publish_content"])
        self.assertNotIn("#人工智能", result["publish_content"])
        self.assertEqual(result["publish_content"].count("结果应以实际环境测试为准"), 1)
        self.assertTrue(result["platform_cleanup"]["changed"])
        self.assertEqual(result["platform_cleanup"]["removed_redirect_lines"], 1)
        self.assertEqual(result["platform_cleanup"]["removed_cta_clauses"], 1)
        self.assertEqual(result["platform_cleanup"]["removed_hashtag_tails"], 1)
        self.assertEqual(result["platform_cleanup"]["removed_duplicate_sentences"], 1)
        self.assertEqual(result["offline_preflight"]["tag_preview"]["selected"], ["AI", "接口", "开发者"])
        codes = {item["code"] for item in result["offline_preflight"]["issues"]}
        self.assertNotIn("sohu_promotional_call_to_action", codes)
        self.assertNotIn("sohu_no_public_source_url", codes)
        self.assertTrue(result["offline_preflight"]["passed"])

    def test_sohu_source_url_is_a_warning_but_no_url_is_not_a_warning(self):
        without_url = evaluate_platform_offline_preflight(
            platform="sohu",
            title="企业接口兼容机制说明",
            content="第一段说明问题。\n\n第二段说明机制。\n\n第三段说明适用边界。",
            tags=[],
        )
        without_codes = {item["code"] for item in without_url["issues"]}
        self.assertNotIn("sohu_no_public_source_url", without_codes)
        self.assertTrue(without_url["passed"])

        with_url = evaluate_platform_offline_preflight(
            platform="sohu",
            title="企业接口兼容机制说明",
            content=(
                "第一段说明问题。\n\n第二段说明机制。\n\n"
                "参考资料：产品技术文档：https://example.com/report"
            ),
            tags=[],
        )
        with_codes = {item["code"] for item in with_url["issues"]}
        self.assertIn("sohu_external_url_review_risk", with_codes)
        self.assertNotIn("sohu_high_confidence_external_redirect", with_codes)
        self.assertTrue(with_url["passed"])

    def test_sohu_high_confidence_diversion_blocks_manual_execution_copy(self):
        result = evaluate_platform_offline_preflight(
            platform="sohu",
            title="企业接口服务说明",
            content=(
                "第一段说明服务边界。\n\n第二段说明兼容机制。\n\n"
                "微信：service_1234，立即咨询。\n\n"
                "官网：https://example.com/product"
            ),
            tags=[],
        )

        codes = {item["code"] for item in result["issues"]}
        self.assertFalse(result["passed"])
        self.assertIn("sohu_contact_information", codes)
        self.assertIn("sohu_high_confidence_external_redirect", codes)
        with self.assertRaisesRegex(PublishContentLimitError, "联系方式"):
            validate_platform_publish_variant(
                platform="sohu",
                title="企业接口服务说明",
                content=(
                    "第一段说明服务边界。\n\n第二段说明兼容机制。\n\n"
                    "微信：service_1234，立即咨询。\n\n"
                    "官网：https://example.com/product"
                ),
                tags=[],
            )

    def test_sohu_seo_question_and_brand_marketing_combo_is_explained(self):
        result = evaluate_platform_offline_preflight(
            platform="sohu",
            title="个人开发者如何选择AI API中转站？",
            content=(
                "本文介绍企业服务的判断维度。\n\n"
                "产品能力需要结合接口兼容和计费方式核验。\n\n"
                "选择结果应以实际测试和合同边界为准。"
            ),
            tags=[],
        )

        codes = {item["code"] for item in result["issues"]}
        self.assertIn("sohu_seo_question_brand_marketing_combo", codes)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
