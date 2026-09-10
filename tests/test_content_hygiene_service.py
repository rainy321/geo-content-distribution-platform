import unittest

from services.content_hygiene_service import constrain_generated_content_length


class ContentLengthConstraintTests(unittest.TestCase):
    def test_short_content_is_unchanged(self):
        result = constrain_generated_content_length("完整短文。", 200)

        self.assertFalse(result.changed)
        self.assertEqual(result.content, "完整短文。")
        self.assertEqual(result.original_length, result.final_length)

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

    def test_invalid_limit_is_rejected(self):
        for limit in (0, True, 1.5):
            with self.subTest(limit=limit), self.assertRaisesRegex(
                ValueError,
                "正整数",
            ):
                constrain_generated_content_length("正文", limit)


if __name__ == "__main__":
    unittest.main()
