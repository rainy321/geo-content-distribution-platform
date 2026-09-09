import unittest

from services.publish_preflight_service import (
    PublishContentLimitError,
    build_platform_publish_variant,
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

    def test_bilibili_description_is_frozen_at_2000_without_hidden_truncation(self):
        exact = "字" * 2000
        result = build_platform_publish_variant(
            platform="bilibili",
            title="视频标题",
            content=exact,
            tags=[],
        )
        self.assertEqual(result["publish_content"], exact)
        self.assertEqual(result["content_policy"]["hard_limit"], 2000)

        with self.assertRaisesRegex(PublishContentLimitError, "当前 2001 字"):
            build_platform_publish_variant(
                platform="bilibili",
                title="视频标题",
                content="原稿",
                tags=[],
                publish_content="字" * 2001,
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

    def test_unbounded_article_platform_keeps_original_content(self):
        original = "## 标题\n\n正文 **强调**"
        result = build_platform_publish_variant(
            platform="zhihu",
            title="测试标题",
            content=original,
            tags=[],
        )

        self.assertEqual(result["publish_content"], original)
        self.assertIsNone(result["content_policy"])
        self.assertFalse(result["adapted"])

    def test_verification_and_frequency_guidance_are_explicitly_non_official(self):
        verification = get_platform_verification_policy("douyin")
        frequency = get_platform_frequency_guidance("douyin")

        self.assertIn("短信验证码", verification["methods"])
        self.assertIn("待人工确认", verification["action"])
        self.assertEqual(frequency["enforcement"], "warning")
        self.assertEqual(frequency["daily_recommended_max"], 3)
        self.assertIn("不是平台官方", frequency["disclaimer"])


if __name__ == "__main__":
    unittest.main()
