import json
import unittest
from pathlib import Path

import requests
from unittest.mock import patch

from services.ai_service import AISettings
from services.content_engine_adapters import (
    ColleagueContentEngineAdapter,
    MockContentEngineAdapter,
    QwenContentEngineAdapter,
)
from services.content_engine_contract import (
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
    ContentGenerationRequest,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "content_engine" / "v1"


class FakeResponse:
    def __init__(self, payload, *, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def iter_content(self, chunk_size=65536):
        encoded = json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        yield from (encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size))

    def close(self):
        self.closed = True


def request():
    return ContentGenerationRequest.create(
        project_context={"id": 1, "name": "XX科技", "product": "企业 AI Agent"},
        topic="企业 AI Agent 落地",
        keywords=["AI Agent"],
        length=1000,
        content_type="行业科普",
        target_platform="zhihu",
        request_id="request-1",
        idempotency_key="idem-1",
    )


class ContentEngineAdapterTests(unittest.TestCase):
    def test_colleague_adapter_sends_internal_contract_headers_and_metadata(self):
        captured = {}
        payload = json.loads((FIXTURE_DIR / "success.json").read_text(encoding="utf-8"))

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(payload, headers={"X-Trace-ID": "header-trace"})

        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            connect_timeout_seconds=2,
            read_timeout_seconds=40,
            http_post=fake_post,
        )
        result = adapter.generate(request())

        self.assertEqual(captured["url"], "http://content-engine:8080/api/v1/generate")
        self.assertEqual(captured["headers"]["X-Request-ID"], "request-1")
        self.assertEqual(captured["headers"]["Idempotency-Key"], "idem-1")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer internal-secret")
        self.assertEqual(captured["timeout"], (2.0, 40.0))
        self.assertFalse(captured["allow_redirects"])
        self.assertTrue(captured["stream"])
        self.assertEqual(result.engine, "colleague")
        self.assertEqual(result.usage["total_tokens"], 3700)

    def test_connection_failure_is_fallback_safe(self):
        def fail_connect(*args, **kwargs):
            try:
                raise ConnectionRefusedError("connection refused")
            except ConnectionRefusedError as cause:
                raise requests.ConnectionError("connection failed") from cause

        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            http_post=fail_connect,
        )
        with self.assertRaises(ContentEngineUnavailableError) as raised:
            adapter.generate(request())
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertFalse(raised.exception.state_unknown)

    def test_read_timeout_is_unknown_and_not_fallback_safe(self):
        def read_timeout(*args, **kwargs):
            raise requests.ReadTimeout("unknown remote outcome")

        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            http_post=read_timeout,
        )
        with self.assertRaises(ContentEngineTimeoutError) as raised:
            adapter.generate(request())
        self.assertFalse(raised.exception.safe_to_fallback)
        self.assertTrue(raised.exception.state_unknown)

    def test_qwen_adapter_preserves_structured_usage_and_actual_model(self):
        def fake_post(*args, **kwargs):
            return FakeResponse(
                {
                    "id": "qwen-request-7",
                    "model": "qwen3.8-max-2026-09",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "Qwen 标题",
                                        "summary": "摘要",
                                        "content": "## 正文\nXX科技与 AI Agent。",
                                        "tags": ["AI Agent"],
                                        "faq": [],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 80,
                        "total_tokens": 200,
                    },
                }
            )

        adapter = QwenContentEngineAdapter(
            settings=AISettings(
                base_url="https://api.example.test/v1",
                api_key="secret",
                model="qwen3.8-max",
            ),
            http_post=fake_post,
        )
        result = adapter.generate(request())

        self.assertEqual(result.engine_version, "qwen3.8-max-2026-09")
        self.assertEqual(result.trace_id, "qwen-request-7")
        self.assertEqual(result.usage["input_tokens"], 120)
        self.assertEqual(result.usage["output_tokens"], 80)

    def test_mock_adapter_is_deterministic(self):
        adapter = MockContentEngineAdapter(
            [{"title": "Mock 标题", "content": "## 正文\nMock 正文"}]
        )
        result = adapter.generate(request())

        self.assertEqual(result.engine, "mock")
        self.assertEqual(len(adapter.calls), 1)

    def test_health_does_not_expose_url_or_token(self):
        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="top-secret-token",
            http_get=lambda *args, **kwargs: FakeResponse(
                {"version": "2026.09"}, status_code=200
            ),
        )
        serialized = json.dumps(adapter.health(), ensure_ascii=False)

        self.assertNotIn("top-secret-token", serialized)
        self.assertNotIn("content-engine:8080", serialized)
        self.assertIn("2026.09", serialized)

    def test_health_closes_response_and_uses_bounded_stream(self):
        response = FakeResponse({"version": "2026.09"}, status_code=200)
        captured = {}

        def fake_get(*args, **kwargs):
            captured.update(kwargs)
            return response

        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            http_get=fake_get,
        )
        self.assertTrue(adapter.health()["ready"])
        self.assertTrue(captured["stream"])
        self.assertTrue(response.closed)

    def test_ambiguous_connection_reset_is_unknown_not_fallback_safe(self):
        def fail_after_send(*args, **kwargs):
            raise requests.ConnectionError("response connection reset")

        adapter = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            http_post=fail_after_send,
        )
        with self.assertRaises(ContentEngineTimeoutError) as raised:
            adapter.generate(request())
        self.assertFalse(raised.exception.safe_to_fallback)
        self.assertTrue(raised.exception.state_unknown)

    def test_colleague_adapter_requires_token_and_allowed_internal_host(self):
        with self.assertRaisesRegex(Exception, "TOKEN"):
            ColleagueContentEngineAdapter(base_url="http://content-engine:8080")
        with self.assertRaisesRegex(Exception, "允许列表"):
            ColleagueContentEngineAdapter(
                base_url="http://169.254.169.254",
                token="internal-secret",
            )

    def test_qwen_health_reports_safe_environment_model_name(self):
        with patch.dict(
            "os.environ",
            {
                "AI_BASE_URL": "https://api.example.test/v1",
                "AI_API_KEY": "hidden",
                "AI_MODEL": "qwen3.8-max",
            },
            clear=True,
        ):
            health = QwenContentEngineAdapter().health()
        self.assertEqual(health["version"], "qwen3.8-max")
        self.assertNotIn("hidden", json.dumps(health))


if __name__ == "__main__":
    unittest.main()
