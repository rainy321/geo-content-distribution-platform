from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import requests
from PIL import Image

from db.createTable import initialize_database
from services.ai_image_generation_service import (
    AIImageGenerationError,
    generate_ai_article_cover,
)
from services.ai_service import AIProviderRateLimitError, AISettings
from services.cover_image_service import store_ai_generated_cover


class SyntheticResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def synthetic_png(width=32, height=24):
    output = BytesIO()
    Image.new("RGB", (width, height), (20, 80, 90)).save(output, format="PNG")
    return output.getvalue()


class AIImageGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.settings = AISettings(
            base_url="https://images.example.test/v1",
            api_key="synthetic-secret",
            model="synthetic-image-model",
            timeout_seconds=3,
            provider="custom",
        )
        self.article = {"title": "Synthetic title"}
        self.project = {
            "name": "Synthetic Company",
            "product": "Synthetic Product",
            "industry": "Testing",
        }

    def test_base64_generation_uses_exact_endpoint_once_and_preserves_usage(self):
        encoded = base64.b64encode(synthetic_png()).decode("ascii")
        post = Mock(
            return_value=SyntheticResponse(
                payload={
                    "id": "provider-image-1",
                    "model": "provider-image-model",
                    "usage": {"prompt_tokens": 4, "total_tokens": 9},
                    "data": [{"b64_json": encoded}],
                }
            )
        )

        result = generate_ai_article_cover(
            article=self.article,
            project=self.project,
            settings=self.settings,
            http_post=post,
        )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.args[0],
            "https://images.example.test/v1/images/generations",
        )
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "synthetic-image-model",
                "prompt": post.call_args.kwargs["json"]["prompt"],
            },
        )
        self.assertNotIn("n", post.call_args.kwargs["json"])
        self.assertNotIn("size", post.call_args.kwargs["json"])
        self.assertNotIn("response_format", post.call_args.kwargs["json"])
        self.assertEqual(result.request_profile, "minimal_openai_images")
        self.assertEqual(result.image_bytes, base64.b64decode(encoded))
        self.assertEqual(result.model, "provider-image-model")
        self.assertEqual(result.usage["input_tokens"], 4)
        self.assertEqual(result.usage["total_tokens"], 9)

    def test_read_timeout_is_unknown_and_never_retried(self):
        post = Mock(side_effect=requests.ReadTimeout("synthetic timeout"))

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(post.call_count, 1)
        self.assertTrue(context.exception.request_sent)
        self.assertTrue(context.exception.outcome_unknown)
        self.assertEqual(
            context.exception.diagnostic_code,
            "response_timeout_unknown",
        )

    def test_seedance_video_model_is_rejected_before_any_image_request(self):
        settings = AISettings(
            base_url="https://tongyuye.com/v1",
            api_key="synthetic-secret",
            model="seedance-5.0",
            timeout_seconds=3,
            provider="custom",
        )
        post = Mock()

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=settings,
                http_post=post,
            )

        post.assert_not_called()
        error = context.exception
        self.assertFalse(error.request_sent)
        self.assertEqual(error.diagnostic_code, "video_model_not_supported")
        self.assertEqual(
            error.endpoint,
            "https://tongyuye.com/v1/images/generations",
        )
        self.assertIn("Seedance 是视频模型", str(error))
        self.assertIn("Seedream", str(error))

    def test_seedream_uses_minimal_gateway_contract_without_optional_fields(self):
        encoded = base64.b64encode(synthetic_png()).decode("ascii")
        settings = AISettings(
            base_url="https://tongyuye.com/v1",
            api_key="synthetic-secret",
            model="Seedream-5.0",
            timeout_seconds=3,
            provider="custom",
        )
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"b64_json": encoded}]},
            )
        )

        result = generate_ai_article_cover(
            article=self.article,
            project=self.project,
            settings=settings,
            http_post=post,
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "Seedream-5.0")
        self.assertNotIn("n", payload)
        self.assertNotIn("size", payload)
        self.assertNotIn("response_format", payload)
        self.assertEqual(result.request_profile, "minimal_openai_images")

    def test_http_failure_is_request_sent_and_not_silently_retried(self):
        post = Mock(return_value=SyntheticResponse(status_code=404))

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(post.call_count, 1)
        self.assertTrue(context.exception.request_sent)
        self.assertFalse(context.exception.outcome_unknown)
        self.assertIn("HTTP 404", str(context.exception))
        self.assertEqual(
            context.exception.diagnostic_code,
            "image_endpoint_not_found",
        )

    def test_http_diagnostics_distinguish_endpoint_auth_permission_and_timeout(self):
        untrusted_body = "bearer-secret and private provider details"
        cases = (
            (400, "invalid_request_or_image_model", "最小必需字段", False),
            (401, "authentication_failed", "API Key", False),
            (403, "permission_denied", "权限", False),
            (404, "image_endpoint_not_found", "/images/generations", False),
            (504, "provider_timeout_unknown", "请求结果未知", True),
        )

        for status, code, message_fragment, outcome_unknown in cases:
            with self.subTest(status=status):
                post = Mock(
                    return_value=SyntheticResponse(
                        status_code=status,
                        payload={"error": untrusted_body},
                    )
                )
                with self.assertRaises(AIImageGenerationError) as context:
                    generate_ai_article_cover(
                        article=self.article,
                        project=self.project,
                        settings=self.settings,
                        http_post=post,
                    )

                error = context.exception
                self.assertEqual(error.diagnostic_code, code)
                self.assertEqual(error.upstream_status, status)
                self.assertEqual(
                    error.endpoint,
                    "https://images.example.test/v1/images/generations",
                )
                self.assertEqual(error.outcome_unknown, outcome_unknown)
                self.assertIn(message_fragment, str(error))
                self.assertNotIn(untrusted_body, str(error))
                self.assertNotIn("synthetic-secret", str(error))

    def test_http_400_body_is_classified_without_exposing_untrusted_text(self):
        cases = (
            (
                {"error": {"code": "model_not_found", "message": "private-model-detail"}},
                "image_model_identifier_rejected",
                "模型标识",
            ),
            (
                {"error": {"param": "size", "message": "unsupported size private-size"}},
                "image_size_rejected",
                "尺寸",
            ),
            (
                {"error": {"param": "response_format", "message": "bad private-format"}},
                "image_response_format_rejected",
                "返回格式",
            ),
            (
                {"error": {"code": "insufficient_balance", "message": "private-balance"}},
                "image_quota_or_balance_unavailable",
                "余额",
            ),
            (
                {"error": {"code": "content_policy", "message": "private-policy"}},
                "image_prompt_rejected",
                "内容安全",
            ),
        )

        for payload, expected_code, expected_hint in cases:
            with self.subTest(expected_code=expected_code):
                post = Mock(
                    return_value=SyntheticResponse(status_code=400, payload=payload),
                )
                with self.assertRaises(AIImageGenerationError) as context:
                    generate_ai_article_cover(
                        article=self.article,
                        project=self.project,
                        settings=self.settings,
                        http_post=post,
                    )

                error = context.exception
                self.assertEqual(error.diagnostic_code, expected_code)
                self.assertIn(expected_hint, str(error))
                self.assertNotIn("private-", str(error))
                self.assertNotIn("synthetic-secret", str(error))

    def test_unknown_http_400_reports_request_profile_without_exposing_body(self):
        settings = AISettings(
            base_url="https://tongyuye.com/v1",
            api_key="synthetic-secret",
            model="Seedream-5.0",
            timeout_seconds=3,
            provider="custom",
        )
        post = Mock(
            return_value=SyntheticResponse(
                status_code=400,
                payload={"error": {"message": "opaque-private-upstream-detail"}},
                headers={"x-one-api-request-id": "req-safe-123"},
            )
        )

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=settings,
                http_post=post,
            )

        error = context.exception
        self.assertEqual(error.diagnostic_code, "invalid_request_or_image_model")
        self.assertEqual(error.request_profile, "minimal_openai_images")
        self.assertEqual(error.provider_request_id, "req-safe-123")
        self.assertIn("model 和 prompt", str(error))
        self.assertNotIn("opaque-private", str(error))

    def test_untrusted_provider_request_id_is_not_exposed(self):
        post = Mock(
            return_value=SyntheticResponse(
                status_code=400,
                payload={"error": {"message": "opaque"}},
                headers={"x-request-id": "bad request id with spaces and secret"},
            )
        )

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(context.exception.provider_request_id, "")

    def test_http_429_preserves_retry_after_without_retrying(self):
        post = Mock(
            return_value=SyntheticResponse(
                status_code=429,
                headers={"Retry-After": "17"},
            )
        )

        with self.assertRaises(AIProviderRateLimitError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(context.exception.retry_after_seconds, 17)
        self.assertIn("HTTP 429", str(context.exception))
        self.assertIn("17 秒", str(context.exception))

    def test_connect_timeout_is_distinct_and_known_not_sent(self):
        post = Mock(side_effect=requests.ConnectTimeout("synthetic connect timeout"))

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
            )

        error = context.exception
        self.assertFalse(error.request_sent)
        self.assertFalse(error.outcome_unknown)
        self.assertEqual(error.diagnostic_code, "connection_timeout")
        self.assertIn("连接生图服务超时", str(error))

    def test_private_remote_image_url_is_rejected_before_download(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://127.0.0.1/image.png"}]}
            )
        )
        get = Mock()

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
            )

        get.assert_not_called()
        self.assertTrue(context.exception.request_sent)
        self.assertIn("私网", str(context.exception))

    def test_proxy_fake_ip_dns_mode_allows_public_hostname_result(self):
        image = synthetic_png()
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://cdn.example.test/image.png"}]}
            )
        )
        download = SyntheticResponse(headers={"Content-Type": "image/png"})
        download.content = image
        get = Mock(return_value=download)
        address_by_host = {
            "cdn.example.test": "198.18.0.41",
            "images.example.test": "198.18.0.42",
            "example.com": "198.18.0.43",
        }

        def resolver(hostname, _port, **_kwargs):
            return [(None, None, None, None, (address_by_host[hostname], 443))]

        result = generate_ai_article_cover(
            article=self.article,
            project=self.project,
            settings=self.settings,
            http_post=post,
            http_get=get,
            dns_resolver=resolver,
        )

        self.assertEqual(result.image_bytes, image)
        self.assertEqual(get.call_args.args[0], "https://cdn.example.test/image.png")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["verify"])

    def test_fake_ip_result_remains_blocked_without_system_wide_proxy_evidence(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://cdn.example.test/image.png"}]}
            )
        )
        get = Mock()
        address_by_host = {
            "cdn.example.test": "198.18.0.41",
            "images.example.test": "8.8.8.8",
            "example.com": "8.8.4.4",
        }

        def resolver(hostname, _port, **_kwargs):
            return [(None, None, None, None, (address_by_host[hostname], 443))]

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
                dns_resolver=resolver,
            )

        get.assert_not_called()
        self.assertIn("私网或保留地址", str(context.exception))

    def test_fake_ip_literal_remains_blocked_even_in_proxy_dns_mode(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://198.18.0.41/image.png"}]}
            )
        )
        get = Mock()
        resolver = Mock()

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
                dns_resolver=resolver,
            )

        get.assert_not_called()
        resolver.assert_not_called()
        self.assertIn("私网或保留地址", str(context.exception))

    def test_ip_mapping_and_encoded_ip_hostnames_are_rejected_before_dns(self):
        hostnames = (
            "127.0.0.1.nip.io",
            "127.0.0.1.evil.example",
            "127-0-0-1.evil.example",
            "7f000001.evil.example",
            "2001-db8--1.evil.example",
        )

        for hostname in hostnames:
            with self.subTest(hostname=hostname):
                post = Mock(
                    return_value=SyntheticResponse(
                        payload={"data": [{"url": f"https://{hostname}/image.png"}]}
                    )
                )
                get = Mock()
                resolver = Mock()

                with self.assertRaises(AIImageGenerationError) as context:
                    generate_ai_article_cover(
                        article=self.article,
                        project=self.project,
                        settings=self.settings,
                        http_post=post,
                        http_get=get,
                        dns_resolver=resolver,
                    )

                get.assert_not_called()
                resolver.assert_not_called()
                self.assertIn("IP 映射域名", str(context.exception))

    def test_mixed_public_and_private_dns_answers_remain_blocked(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://cdn.example.test/image.png"}]}
            )
        )
        get = Mock()

        def resolver(_hostname, _port, **_kwargs):
            return [
                (None, None, None, None, ("8.8.8.8", 443)),
                (None, None, None, None, ("10.0.0.8", 443)),
            ]

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
                dns_resolver=resolver,
            )

        get.assert_not_called()
        self.assertIn("私网或保留地址", str(context.exception))

    def test_scoped_link_local_ipv6_answer_is_not_ignored(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://cdn.example.test/image.png"}]}
            )
        )
        get = Mock()

        def resolver(_hostname, _port, **_kwargs):
            return [
                (None, None, None, None, ("2606:4700:4700::1111", 443, 0, 0)),
                (None, None, None, None, ("fe80::1%12", 443, 0, 12)),
            ]

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
                dns_resolver=resolver,
            )

        get.assert_not_called()
        self.assertIn("私网或保留地址", str(context.exception))

    def test_same_origin_relative_image_url_is_resolved_safely(self):
        image = synthetic_png()
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "/generated/image.png"}]}
            )
        )
        download = SyntheticResponse(headers={"Content-Type": "image/png"})
        download.content = image
        get = Mock(return_value=download)

        def resolver(_hostname, _port, **_kwargs):
            return [(None, None, None, None, ("8.8.8.8", 443))]

        result = generate_ai_article_cover(
            article=self.article,
            project=self.project,
            settings=self.settings,
            http_post=post,
            http_get=get,
            dns_resolver=resolver,
        )

        self.assertEqual(result.image_bytes, image)
        self.assertEqual(
            get.call_args.args[0],
            "https://images.example.test/generated/image.png",
        )

    def test_image_download_rejects_explicit_html_content_type(self):
        post = Mock(
            return_value=SyntheticResponse(
                payload={"data": [{"url": "https://cdn.example.test/image.png"}]}
            )
        )
        download = SyntheticResponse(
            headers={"Content-Type": "text/html; charset=utf-8"}
        )
        download.content = b"<html>not an image</html>"
        get = Mock(return_value=download)

        def resolver(_hostname, _port, **_kwargs):
            return [(None, None, None, None, ("8.8.8.8", 443))]

        with self.assertRaises(AIImageGenerationError) as context:
            generate_ai_article_cover(
                article=self.article,
                project=self.project,
                settings=self.settings,
                http_post=post,
                http_get=get,
                dns_resolver=resolver,
            )

        self.assertIn("网页或非图片内容", str(context.exception))

    def test_provider_image_is_normalized_before_entering_material_library(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        database_path = root / "database.db"
        media_root = root / "media"
        initialize_database(database_path)

        record = store_ai_generated_cover(
            database_path,
            media_root=media_root,
            article={"id": 7, "title": "Synthetic title", "tags": []},
            project=self.project,
            image_bytes=synthetic_png(64, 32),
        )

        self.assertEqual(record["source"], "generated_ai")
        saved = media_root / record["file_path"]
        self.assertTrue(saved.is_file())
        with Image.open(saved) as image:
            self.assertEqual(image.size, (1200, 628))
            self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
