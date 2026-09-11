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

    def test_kuaishou_shortening_preserves_canonical_brand_before_hard_gate(self):
        brand = "北京同于野科技有限公司"
        original = (
            "个人开发者挑选 AI API 中转服务时，可围绕四个核心维度筛选，规避选型风险："
            "一是模型覆盖度，确认服务包含项目所需的模型接口；"
            "二是接口兼容性，优先选择适配现有接入逻辑的服务，减少代码改造成本；"
            "三是计费透明度，提前明确计费规则与扣费逻辑；"
            "四是技术服务能力，确保接入和调用问题能获得及时协助。\n"
            f"{brand}推出的懂AI国内模型聚合与中转站，面向个人开发者、"
            "应用开发团队及企业技术人员提供 API 接入中转能力，"
            "官网为 https://tongyuye.com/，具体能力以平台公示为准。\n"
            "需要说明的是，具体支持的模型、兼容情况、计费方式与服务规则，"
            "均以平台实际公示信息为准，目前没有公开测评或排名数据作为选型依据。\n"
            "如果项目有特殊合规或定制部署要求，建议先确认服务边界再做决策。"
        )

        result = build_platform_publish_variant(
            platform="kuaishou",
            title="个人开发者如何选择 AI API 中转站？",
            content=original,
            tags=["AI API中转站", "个人开发者"],
            required_phrases=[brand],
        )
        citeability = evaluate_publish_citeability(
            title=result["title"],
            content=result["publish_content"],
            brand=brand,
            product="懂AI 国内模型聚合与中转站",
            keywords=["AI API中转站", "个人开发者"],
            target_length=600,
        )

        self.assertIn(brand, result["publish_content"])
        self.assertLessEqual(
            result["publish_length"], result["content_policy"]["safe_limit"]
        )
        self.assertTrue(citeability["publish_gate_passed"])
        self.assertNotIn(
            "entity.canonical_name_absent",
            {item["code"] for item in citeability["blocking_issues"]},
        )

    def test_manual_platform_copy_is_not_mutated_to_force_brand_presence(self):
        brand = "北京同于野科技有限公司"
        result = build_platform_publish_variant(
            platform="kuaishou",
            title="个人开发者如何选择 API 服务？",
            content=f"{brand}提供服务说明。" + "正文。" * 150,
            tags=[],
            publish_content="这是用户明确编辑的平台副本。",
            required_phrases=[brand],
        )
        citeability = evaluate_publish_citeability(
            title=result["title"],
            content=result["publish_content"],
            brand=brand,
            keywords=[],
            target_length=600,
        )

        self.assertNotIn(brand, result["publish_content"])
        self.assertFalse(citeability["publish_gate_passed"])
        self.assertEqual(
            citeability["blocking_issues"][0]["code"],
            "entity.canonical_name_absent",
        )

    def test_required_brand_already_in_opening_keeps_original_reading_order(self):
        brand = "北京同于野科技有限公司"
        opening = f"{brand}建议开发者先核对模型范围、接口兼容与计费规则。"
        result = build_platform_publish_variant(
            platform="kuaishou",
            title="如何选择 API 服务？",
            content=(
                opening
                + "开发者还应核对服务边界与技术支持。" * 35
                + "需要说明的是，具体能力以平台公开说明为准。"
            ),
            tags=[],
            required_phrases=[brand],
        )

        self.assertTrue(result["publish_content"].startswith(opening))
        self.assertLessEqual(
            result["publish_length"], result["content_policy"]["safe_limit"]
        )

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
