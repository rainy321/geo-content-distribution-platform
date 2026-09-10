import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.ai_service import (
    AICompletionResult,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AISettings,
)


class AIVisibilitySamplingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "sampling-api.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "Synthetic Company",
                        "https://synthetic.example.test",
                        "Synthetic Product",
                        "Testing",
                        "Synthetic API test only.",
                        "[]",
                        json.dumps(["Synthetic Rival"]),
                    ),
                )
                self.project_id = int(cursor.lastrowid)
        self.original_config = {
            "DATABASE_PATH": app.config.get("DATABASE_PATH"),
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "AI_RATE_LIMIT_PER_MINUTE": app.config.get("AI_RATE_LIMIT_PER_MINUTE"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            DATABASE_PATH=self.database_path,
            ACCESS_CONTROL_ENABLED=False,
            AI_RATE_LIMIT_PER_MINUTE=0,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def payload(self, **overrides):
        value = {
            "project_id": self.project_id,
            "prompt": "Which synthetic product is worth considering?",
            "sample_count": 2,
            "aliases": ["Synthetic Product"],
            "competitors": ["Synthetic Rival"],
            "persist": True,
            "confirm_external_call": True,
            "ai_config": {
                "base_url": "https://api.example.test/v1",
                "api_key": "browser-session-secret",
                "model": "sample-model",
            },
        }
        value.update(overrides)
        return value

    def headers(self, **overrides):
        value = {
            "X-Request-ID": "api-sampling-request-1",
            "Idempotency-Key": "api-sampling-key-1",
        }
        value.update(overrides)
        return value

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_live_sample_contract_persists_and_never_exposes_api_key(self, sampler):
        sampler.side_effect = [
            AICompletionResult(
                content=(
                    "Synthetic Product is an option; compare Synthetic Rival. "
                    "https://synthetic.example.test/docs"
                ),
                model="sample-model",
                provider_request_id="provider-1",
                elapsed_ms=10,
                usage={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
            ),
            AICompletionResult(
                content="Synthetic Rival is known; Synthetic Product is also available.",
                model="sample-model",
                provider_request_id="provider-2",
                elapsed_ms=11,
                usage=None,
            ),
        ]

        response = self.client.post(
            "/api/geo/visibility/sample",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "api-sampling-request-1")
        self.assertEqual(response.headers["Idempotency-Key"], "api-sampling-key-1")
        self.assertEqual(response.headers["Idempotency-Replayed"], "false")
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["completed_sample_count"], 2)
        self.assertTrue(data["persisted"])
        self.assertEqual(data["data_disclosure"]["sent_to_provider"], ["prompt"])
        self.assertFalse(data["grounding"]["web_search_requested"])
        self.assertFalse(data["grounding"]["provider_grounding_guaranteed"])
        self.assertEqual(sampler.call_count, 2)
        for call in sampler.call_args_list:
            self.assertEqual(set(call.kwargs), {"prompt", "settings"})
            self.assertEqual(call.kwargs["prompt"], self.payload()["prompt"])
            self.assertIsInstance(call.kwargs["settings"], AISettings)

        serialized = response.get_data(as_text=True)
        self.assertNotIn("browser-session-secret", serialized)
        with closing(sqlite3.connect(self.database_path)) as conn:
            ledger_rows = conn.execute(
                "SELECT operation, usage_status FROM ai_usage_ledger ORDER BY id"
            ).fetchall()
            stored_result = conn.execute(
                "SELECT result_json FROM ai_visibility_sample_requests"
            ).fetchone()[0]
        self.assertEqual(
            ledger_rows,
            [("visibility_sample", "reported"), ("visibility_sample", "unknown")],
        )
        self.assertNotIn("browser-session-secret", stored_result)

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_same_key_replays_saved_result_without_provider_call(self, sampler):
        sampler.return_value = AICompletionResult(
            content="Synthetic Product",
            model="sample-model",
            provider_request_id="provider-1",
            elapsed_ms=10,
            usage={"total_tokens": 2},
        )
        payload = self.payload(sample_count=1, persist=False)
        first = self.client.post(
            "/api/geo/visibility/sample", json=payload, headers=self.headers()
        )
        second = self.client.post(
            "/api/geo/visibility/sample",
            json=payload,
            headers=self.headers(**{"X-Request-ID": "api-sampling-retry"}),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(sampler.call_count, 1)
        self.assertEqual(second.headers["Idempotency-Replayed"], "true")
        self.assertEqual(second.headers["X-Request-ID"], "api-sampling-request-1")
        self.assertEqual(second.get_json(), first.get_json())

        resumed = self.client.get(
            "/api/geo/visibility/sample/api-sampling-request-1"
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.headers["Idempotency-Replayed"], "true")
        self.assertEqual(resumed.get_json()["data"]["status"], "completed")
        self.assertEqual(sampler.call_count, 1)

    def test_resume_endpoint_reports_started_request_without_provider_call(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility_sample_requests (
                        project_id, request_id, idempotency_key,
                        request_fingerprint, status
                    ) VALUES (?, ?, ?, ?, 'started')
                    """,
                    (
                        self.project_id,
                        "resume-started-request",
                        "resume-started-key",
                        "a" * 64,
                    ),
                )

        response = self.client.get(
            "/api/geo/visibility/sample/resume-started-request"
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["Idempotency-Replayed"], "true")
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "processing")
        self.assertFalse(data["retry_policy"]["automatic_retry"])
        self.assertIn("不会重新调用模型", response.get_json()["msg"])

    def test_resume_endpoint_rejects_unknown_or_invalid_identity(self):
        missing = self.client.get(
            "/api/geo/visibility/sample/missing-request"
        )
        invalid = self.client.get(
            "/api/geo/visibility/sample/not%20valid"
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid.status_code, 400)

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_partial_timeout_returns_need_action_and_stops(self, sampler):
        sampler.side_effect = [
            AICompletionResult(
                content="Synthetic Product",
                model="sample-model",
                provider_request_id="provider-1",
                elapsed_ms=10,
                usage=None,
            ),
            AIReadTimeoutError("模型请求结果未知，请勿自动重试"),
        ]

        response = self.client.post(
            "/api/geo/visibility/sample",
            json=self.payload(sample_count=5, persist=False),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 207)
        self.assertEqual(sampler.call_count, 2)
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "partial")
        self.assertTrue(data["need_action"])
        self.assertEqual(data["failure"]["code"], "provider_outcome_unknown")
        self.assertFalse(data["retry_policy"]["automatic_retry"])

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_first_provider_rate_limit_is_need_action_with_retry_after(self, sampler):
        sampler.side_effect = AIProviderRateLimitError(
            "AI 服务已达到限流，请稍后再试", retry_after_seconds=17
        )

        response = self.client.post(
            "/api/geo/visibility/sample",
            json=self.payload(sample_count=3, persist=False),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "17")
        self.assertEqual(sampler.call_count, 1)
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "need_action")
        self.assertEqual(data["completed_sample_count"], 0)
        self.assertEqual(data["unattempted_sample_count"], 2)
        self.assertEqual(data["failure"]["code"], "provider_rate_limited")

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_rejects_missing_confirmation_key_and_invalid_contract_before_call(
        self, sampler
    ):
        cases = [
            (self.payload(confirm_external_call=False), self.headers(), "confirm_external_call"),
            (self.payload(), {"X-Request-ID": "request-only"}, "Idempotency-Key"),
            (self.payload(sample_count=6), self.headers(), "sample_count"),
            (self.payload(aliases="not-an-array"), self.headers(), "aliases"),
            (self.payload(extra_project_context={"secret": True}), self.headers(), "不支持的字段"),
        ]
        for payload, headers, expected in cases:
            with self.subTest(expected=expected):
                response = self.client.post(
                    "/api/geo/visibility/sample", json=payload, headers=headers
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.get_json()["msg"])
        sampler.assert_not_called()

    @patch("services.ai_visibility_sampling_service.sample_ai_prompt_detailed")
    def test_request_scoped_ai_config_is_validated_before_call(self, sampler):
        response = self.client.post(
            "/api/geo/visibility/sample",
            json=self.payload(
                ai_config={
                    "base_url": "http://localhost:9999/v1",
                    "api_key": "secret",
                    "model": "sample-model",
                }
            ),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("HTTPS", response.get_json()["msg"])
        sampler.assert_not_called()

    def test_existing_operator_gate_protects_live_sampling(self):
        app.config["ACCESS_CONTROL_ENABLED"] = True

        response = self.client.post(
            "/api/geo/visibility/sample",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["data"]["authentication_required"])


if __name__ == "__main__":
    unittest.main()
