import unittest

from services.content_hygiene_service import (
    authoring_visible_length,
    constrain_generated_content_length,
    ensure_owned_website_reference,
)


class ContentLengthConstraintTests(unittest.TestCase):
    def test_owned_https_site_is_attached_once_and_explicitly_self_owned(self):
        first = ensure_owned_website_reference(
            "直接回答。",
            website="https://Brand.Example/guide#section",
            brand="示例品牌",
        )
        second = ensure_owned_website_reference(
            first.content,
            website="https://brand.example/guide",
            brand="示例品牌",
        )

        self.assertTrue(first.appended)
        self.assertIn("项目自有来源", first.content)
        self.assertIn("https://brand.example/guide", first.content)
        self.assertEqual(
            first.source,
            {"title": "示例品牌官网", "url": "https://brand.example/guide"},
        )
        self.assertFalse(second.appended)
        self.assertEqual(second.content.count("https://brand.example/guide"), 1)

    def test_owned_reference_never_upgrades_http_or_accepts_credentials(self):
        for website in (
            "http://example.com",
            "https://user:secret@example.com",
            "https://localhost",
            "https://example.com:8443",
        ):
            with self.subTest(website=website):
                result = ensure_owned_website_reference(
                    "正文",
                    website=website,
                    brand="示例品牌",
                )
                self.assertFalse(result.appended)
                self.assertIsNone(result.source)
                self.assertEqual(result.content, "正文")

    def test_short_content_is_unchanged(self):
        result = constrain_generated_content_length("完整短文。", 200)

        self.assertFalse(result.changed)
        self.assertEqual(result.content, "完整短文。")
        self.assertEqual(result.original_length, result.final_length)

    def test_generation_budget_uses_non_whitespace_length_without_stripping_markdown(self):
        content = "## 直接回答\n\n" + ("可核验内容    " * 30)

        self.assertGreater(len(content), 200)
        self.assertLessEqual(authoring_visible_length(content), 200)

        result = constrain_generated_content_length(content, 200)

        self.assertFalse(result.changed)
        self.assertEqual(result.content, content.strip())
        self.assertEqual(result.final_length, authoring_visible_length(content))

    def test_long_single_sentence_never_exceeds_limit(self):
        result = constrain_generated_content_length("字" * 500, 200)

        self.assertTrue(result.changed)
        self.assertLessEqual(result.final_length, 200)
        self.assertTrue(result.content.endswith("…"))

    def test_shortening_retains_a_small_evidence_boundary(self):
        content = (
            "直接回答问题。" * 80
            + "\n限制与风险：结果取决于输入资料并需要人工复核。"
        )
        result = constrain_generated_content_length(content, 200)

        self.assertLessEqual(result.final_length, 200)
        self.assertIn("限制与风险", result.content)

    def test_shortening_uses_at_least_eighty_percent_of_budget(self):
        content = (
            ("第一部分说明。" * 12)
            + ("第二部分包含更长的说明但仍需要自然裁剪" * 12)
            + "\n限制与风险：结果取决于输入资料并需要人工复核。"
        )

        result = constrain_generated_content_length(content, 200)

        self.assertGreaterEqual(result.final_length, 160)
        self.assertLessEqual(result.final_length, 200)
        self.assertEqual(result.final_length, authoring_visible_length(result.content))
        self.assertRegex(result.content, r"[。！？!?；;，,：:…]$")

    def test_complete_sentences_win_when_they_already_fill_eighty_percent(self):
        sentence = ("完整说明" * 8) + "。"
        content = (
            (sentence * 6)
            + "\n限制与风险：请以可核验资料为准。"
        )

        result = constrain_generated_content_length(content, 200)

        self.assertGreaterEqual(result.final_length, 160)
        self.assertLessEqual(result.final_length, 200)
        self.assertNotIn("…", result.content)
        self.assertTrue(all(line.endswith("。") for line in result.content.splitlines()))

    def test_invalid_limit_is_rejected(self):
        for limit in (0, True, 1.5):
            with self.subTest(limit=limit), self.assertRaisesRegex(
                ValueError,
                "正整数",
            ):
                constrain_generated_content_length("正文", limit)


if __name__ == "__main__":
    unittest.main()
