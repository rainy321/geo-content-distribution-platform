from __future__ import annotations

import unittest

from services.content_diversity_service import (
    ContentDiversityError,
    build_content_diversity_profile,
    build_diversity_instruction,
)


class ContentDiversityServiceTests(unittest.TestCase):
    def test_same_seed_and_brief_are_deterministic_and_auditable(self):
        first = build_content_diversity_profile(
            seed="stable-request-1",
            topic="企业如何选择模型中转站",
            content_type="解决方案",
            target_platform="知乎",
        )
        second = build_content_diversity_profile(
            seed="stable-request-1",
            topic="企业如何选择模型中转站",
            content_type="解决方案",
            target_platform="知乎",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["fact_policy"], "input_only_no_invention")
        self.assertEqual(len(first["seed_fingerprint"]), 12)
        self.assertNotIn("stable-request-1", str(first))

    def test_topic_or_seed_changes_the_compiled_profile(self):
        profiles = {
            str(
                build_content_diversity_profile(
                    seed=f"seed-{index}",
                    topic=f"topic-{index}",
                )
            )
            for index in range(8)
        }

        self.assertGreater(len(profiles), 1)

    def test_instruction_preserves_hard_no_invention_boundary(self):
        profile = build_content_diversity_profile(
            seed="boundary-test",
            topic="synthetic topic",
        )

        instruction = build_diversity_instruction(profile)

        self.assertIn("只改变表达，不改变事实", instruction)
        self.assertIn("不得为了差异化虚构", instruction)
        self.assertIn(profile["angle"]["label"], instruction)

    def test_rejects_multiline_or_oversized_operator_seed(self):
        for value in ("bad\nseed", "x" * 129):
            with self.subTest(value=value[:10]):
                with self.assertRaises(ContentDiversityError):
                    build_content_diversity_profile(seed=value, topic="topic")


if __name__ == "__main__":
    unittest.main()
