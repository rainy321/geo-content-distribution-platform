import os
import unittest
from unittest.mock import patch

from services.ai_service import (
    AIConfigurationError,
    AIServiceError,
    AISettings,
    generate_geo_content,
    optimize_geo_content,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "name": "XX科技",
            "website": "https://example.test",
            "product": "企业 AI Agent",
            "industry": "人工智能",
            "description": "帮助企业构建智能服务流程。",
        }
        self.settings = AISettings(
            base_url="https://api.example.test/v1",
            api_key="test-key",
            model="test-model",
            timeout_seconds=15,
        )

    def test_calls_openai_compatible_endpoint_and_parses_json(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": """```json
                                {
                                  "title": "企业 AI Agent 如何落地",
                                  "summary": "一篇实践指南",
                                  "content": "## 什么是企业 AI Agent\\n正文",
                                  "tags": ["#AI Agent", "企业智能体"],
                                  "faq": [{"q": "适合谁？", "a": "成长型企业。"}]
                                }
                                ```"""
                            }
                        }
                    ]
                }
            )

        result = generate_geo_content(
            project=self.project,
            topic="企业 AI Agent 落地",
            keywords=["AI Agent", "企业智能体"],
            length=1000,
            content_type="行业科普",
            target_platform="知乎",
            settings=self.settings,
            http_post=fake_post,
        )

        self.assertEqual(
            captured["url"],
            "https://api.example.test/v1/chat/completions",
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["json"]["model"], "test-model")
        self.assertEqual(captured["timeout"], 15)
        self.assertFalse(captured["allow_redirects"])
        prompt = captured["json"]["messages"][1]["content"]
        self.assertIn("XX科技", prompt)
        self.assertIn("企业 AI Agent 落地", prompt)
        self.assertIn("正文约 1000 字", prompt)
        self.assertIn("目标平台：知乎", prompt)
        self.assertEqual(result["title"], "企业 AI Agent 如何落地")
        self.assertEqual(result["tags"], ["AI Agent", "企业智能体"])
        self.assertEqual(
            result["faq"],
            [{"question": "适合谁？", "answer": "成长型企业。"}],
        )

    def test_falls_back_to_plain_text_when_response_is_not_json(self):
        def fake_post(*args, **kwargs):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "# 企业智能体指南\n\n这是一篇普通文本正文。"
                            }
                        }
                    ]
                }
            )

        result = generate_geo_content(
            project=self.project,
            topic="企业智能体指南",
            keywords=["AI Agent", "AI Agent", "企业智能体"],
            length=600,
            content_type="行业科普",
            settings=self.settings,
            http_post=fake_post,
        )

        self.assertEqual(result["title"], "企业智能体指南")
        self.assertEqual(result["content"], "# 企业智能体指南\n\n这是一篇普通文本正文。")
        self.assertEqual(result["tags"], ["AI Agent", "企业智能体"])
        self.assertEqual(result["faq"], [])

    def test_requires_all_environment_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                AIConfigurationError,
                "AI_BASE_URL, AI_API_KEY, AI_MODEL",
            ):
                AISettings.from_environment()

    def test_builds_safe_request_scoped_settings(self):
        settings = AISettings.from_mapping(
            {
                "base_url": "https://api.example.com/compatible-mode/v1",
                "api_key": "secret-value",
                "model": "custom-model",
            }
        )

        self.assertEqual(
            settings.base_url,
            "https://api.example.com/compatible-mode/v1",
        )
        self.assertEqual(settings.api_key, "secret-value")
        self.assertEqual(settings.model, "custom-model")

    def test_rejects_unsafe_custom_provider_urls(self):
        for base_url in (
            "http://api.example.com/v1",
            "https://localhost/v1",
            "https://127.0.0.1/v1",
            "https://10.0.0.8/v1",
            "https://metadata.internal/v1",
            "https://user:password@example.com/v1",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(AIConfigurationError):
                    AISettings.from_mapping(
                        {
                            "base_url": base_url,
                            "api_key": "secret-value",
                            "model": "custom-model",
                        }
                    )

    def test_reports_environment_configuration_without_exposing_values(self):
        with patch.dict(
            os.environ,
            {
                "AI_BASE_URL": "https://api.example.com/v1",
                "AI_API_KEY": "secret-value",
                "AI_MODEL": "custom-model",
            },
            clear=True,
        ):
            self.assertTrue(AISettings.environment_is_configured())

    def test_rejects_invalid_provider_payload(self):
        def fake_post(*args, **kwargs):
            return FakeResponse({"choices": ["invalid"]})

        with self.assertRaisesRegex(AIServiceError, "返回格式无效"):
            generate_geo_content(
                project=self.project,
                topic="测试",
                keywords=[],
                length=600,
                content_type="行业科普",
                settings=self.settings,
                http_post=fake_post,
            )

    def test_does_not_follow_provider_redirects(self):
        def fake_post(*args, **kwargs):
            self.assertFalse(kwargs["allow_redirects"])
            return FakeResponse({}, status_code=302, text="redirect")

        with self.assertRaisesRegex(AIServiceError, "重定向"):
            generate_geo_content(
                project=self.project,
                topic="测试",
                keywords=[],
                length=600,
                content_type="行业科普",
                settings=self.settings,
                http_post=fake_post,
            )

    def test_optimizes_from_score_and_suggestions_without_saving(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": """{
                                  "title": "企业 AI Agent 实施指南",
                                  "summary": "优化后的摘要",
                                  "content": "## 实施步骤\\n优化后的完整正文。",
                                  "tags": ["AI Agent"],
                                  "faq": []
                                }"""
                            }
                        }
                    ]
                }
            )

        result = optimize_geo_content(
            article={
                "title": "AI Agent",
                "summary": "原摘要",
                "content": "原始正文",
                "tags": ["AI Agent"],
            },
            score={
                "score": 42,
                "suggestions": ["建议增加清晰的 H2/H3 小标题"],
            },
            settings=self.settings,
            http_post=fake_post,
        )

        prompt = captured["json"]["messages"][1]["content"]
        self.assertIn("原始正文", prompt)
        self.assertIn("42 / 100", prompt)
        self.assertIn("建议增加清晰的 H2/H3 小标题", prompt)
        self.assertIn("不要编造", prompt)
        self.assertEqual(captured["json"]["temperature"], 0.45)
        self.assertEqual(result["title"], "企业 AI Agent 实施指南")
        self.assertEqual(result["content"], "## 实施步骤\n优化后的完整正文。")


if __name__ == "__main__":
    unittest.main()
