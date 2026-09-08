import json
import unittest
from pathlib import Path

from services.content_engine_contract import (
    ContentEngineProtocolError,
    ContentGenerationRequest,
    ContentGenerationResult,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "content_engine" / "v1"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class ContentEngineContractTests(unittest.TestCase):
    def request(self, *, request_id, topic="测试主题"):
        return ContentGenerationRequest.create(
            project_context={"id": 7, "name": "XX科技", "product": "企业 AI Agent"},
            topic=topic,
            keywords=["AI Agent"],
            length=1000,
            content_type="行业科普",
            target_platform="zhihu",
            request_id=request_id,
        )

    def test_fingerprint_excludes_transport_request_id(self):
        first = self.request(request_id="request-a")
        second = self.request(request_id="request-b")

        self.assertNotEqual(first.provider_payload()["request_id"], second.provider_payload()["request_id"])
        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertNotEqual(
            first.fingerprint(),
            self.request(request_id="request-c", topic="另一个主题").fingerprint(),
        )

    def test_normalizes_success_fixture_without_promoting_provider_score(self):
        result = ContentGenerationResult.from_payload(
            load_fixture("success.json"),
            default_engine="colleague",
            default_engine_version="unknown",
            request_id="request-a",
            elapsed_ms=80,
        )

        self.assertEqual(result.title, "企业 AI Agent 如何落地")
        self.assertEqual(result.usage["input_tokens"], 2100)
        self.assertEqual(result.usage["output_tokens"], 1600)
        self.assertEqual(result.provider_geo_score, 91)
        self.assertNotIn("geo_score", result.public_payload())

    def test_rejects_missing_required_title(self):
        with self.assertRaisesRegex(ContentEngineProtocolError, "缺少标题"):
            ContentGenerationResult.from_payload(
                load_fixture("missing_title.json"),
                default_engine="colleague",
                default_engine_version="unknown",
                request_id="request-a",
                elapsed_ms=10,
            )

    def test_optional_usage_is_null_not_zero(self):
        result = ContentGenerationResult.from_payload(
            load_fixture("partial_optional.json"),
            default_engine="colleague",
            default_engine_version="unknown",
            request_id="request-a",
            elapsed_ms=10,
        )

        public = result.public_payload()["generation"]
        self.assertIsNone(public["usage"])
        self.assertEqual(public["usage_status"], "unknown")
        self.assertTrue(any("Token" in warning for warning in public["warnings"]))

    def test_rejects_incompatible_contract_version(self):
        payload = load_fixture("success.json")
        payload["contract_version"] = "2.0"
        with self.assertRaisesRegex(ContentEngineProtocolError, "版本不兼容"):
            ContentGenerationResult.from_payload(
                payload,
                default_engine="colleague",
                default_engine_version="unknown",
                request_id="request-a",
                elapsed_ms=10,
            )

    def test_provider_cannot_replace_local_request_id(self):
        payload = load_fixture("success.json")
        payload["data"]["request_id"] = "different-provider-id"
        result = ContentGenerationResult.from_payload(
            payload,
            default_engine="colleague",
            default_engine_version="unknown",
            request_id="local-request-id",
            elapsed_ms=10,
        )
        self.assertEqual(result.request_id, "local-request-id")
        self.assertTrue(any("request_id" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
