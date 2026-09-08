import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.content_engine_adapters import MockContentEngineAdapter
from services.content_engine_contract import (
    ContentEngineProtocolError,
    ContentEngineRateLimitError,
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
)
from services.content_generation_service import ContentGenerationService


VALID_RESULT = {
    "title": "企业 AI Agent 实践指南",
    "summary": "一份可核验的实施指南。",
    "content": (
        "XX科技围绕 AI Agent 给出实施方法。\n\n"
        "## 实施步骤\n2026 年建议分 3 个阶段验证。\n\n"
        "## FAQ\n问：如何开始？\n答：先验证最小流程。\n"
        "问：如何验收？\n答：保留运行记录。\n\n"
        "## 总结\n以可追踪结果为准。来源：https://example.test/report"
    ),
    "tags": ["AI Agent"],
    "faq": [{"question": "如何开始？", "answer": "先验证最小流程。"}],
    "sources": [{"title": "测试来源", "url": "https://example.test/report"}],
    "engine": "mock",
    "engine_version": "1.0-test",
    "usage": {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
    "provider_geo_score": 88,
}


class ContentGenerationApiV1Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    "INSERT INTO projects (name, keywords) VALUES (?, ?)",
                    (
                        "XX科技",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                    ),
                ).lastrowid
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def _post(self, *, topic="AI Agent 落地", key="contract-test-key", request_id="req-1"):
        return self.client.post(
            "/api/articles/generate",
            headers={"Idempotency-Key": key, "X-Request-ID": request_id},
            json={
                "project_id": self.project_id,
                "topic": topic,
                "keywords": ["AI Agent", "企业智能体"],
                "target_platform": "知乎",
            },
        )

    def test_generates_scores_and_replays_without_second_provider_call(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(request_id="req-first")
            replay = self._post(request_id="req-replay")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(adapter.calls), 1)
        data = first.get_json()["data"]
        self.assertGreaterEqual(data["geo_score"], 0)
        self.assertIn("dimensions", data["geo_analysis"])
        self.assertEqual(data["generation"]["engine"], "mock")
        self.assertEqual(data["generation"]["usage"]["total_tokens"], 46)
        self.assertEqual(data["generation"]["provider_geo_score"], 88.0)
        self.assertEqual(
            replay.get_json()["data"]["generation"]["run_id"],
            data["generation"]["run_id"],
        )
        self.assertEqual(first.headers["Idempotency-Replayed"], "false")
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(replay.headers["X-Request-ID"], "req-first")
        self.assertEqual(
            replay.get_json()["data"]["generation"]["request_id"],
            "req-first",
        )

        run = self.client.get(
            f"/api/content-generation/runs/{data['generation']['run_id']}"
        ).get_json()["data"]
        self.assertEqual(run["sources"], VALID_RESULT["sources"])
        self.assertEqual(run["score_context"]["keywords"], ["AI Agent", "企业智能体"])

    def test_request_id_cannot_be_rebound_to_a_different_idempotency_key(self):
        second_result = {**VALID_RESULT, "title": "第二次独立生成"}
        adapter = MockContentEngineAdapter(
            [VALID_RESULT, second_result],
            version="1.0-test",
        )
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(key="key-a", request_id="request-r")
            conflict = self._post(key="key-b", request_id="request-r")
            independent = self._post(key="key-b", request_id="request-r2")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["Idempotency-Key"], "key-a")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.headers["Idempotency-Key"], "key-a")
        self.assertEqual(
            conflict.get_json()["data"]["error_code"],
            "idempotency_conflict",
        )
        self.assertEqual(independent.status_code, 200)
        self.assertEqual(independent.headers["Idempotency-Key"], "key-b")
        self.assertEqual(independent.get_json()["data"]["title"], "第二次独立生成")
        self.assertEqual(len(adapter.calls), 2)

    def test_success_replay_does_not_depend_on_current_provider_configuration(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(key="durable-key", request_id="request-original")
        with patch(
            "sau_backend._create_content_generation_service",
            side_effect=AssertionError("provider factory must not run during replay"),
        ):
            replay = self._post(key="durable-key", request_id="request-replay")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(replay.headers["Idempotency-Key"], "durable-key")
        self.assertEqual(len(adapter.calls), 1)

    def test_success_replay_does_not_consume_ai_rate_limit_twice(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        limiter = Mock()
        limiter.consume.return_value = SimpleNamespace(
            allowed=True,
            retry_after_seconds=0,
        )
        with patch.dict(
            app.config,
            {"AI_RATE_LIMIT_PER_MINUTE": 1, "AI_RATE_LIMITER": limiter},
        ), patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(key="rate-replay", request_id="req-rate-first")
            replay = self._post(key="rate-replay", request_id="req-rate-replay")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(limiter.consume.call_count, 1)
        self.assertEqual(len(adapter.calls), 1)

    def test_rate_limit_error_replay_preserves_status_and_retry_after(self):
        adapter = MockContentEngineAdapter(
            [ContentEngineRateLimitError("provider limited", retry_after_seconds=17)]
        )
        service = ContentGenerationService(self.db_path, adapter)
        limiter = Mock()
        limiter.consume.return_value = SimpleNamespace(
            allowed=True,
            retry_after_seconds=0,
        )
        with patch.dict(
            app.config,
            {"AI_RATE_LIMIT_PER_MINUTE": 1, "AI_RATE_LIMITER": limiter},
        ), patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(key="provider-limit", request_id="req-limit-first")
            replay = self._post(key="provider-limit", request_id="req-limit-replay")

        self.assertEqual(first.status_code, 429)
        self.assertEqual(replay.status_code, 429)
        self.assertEqual(first.headers["Retry-After"], "17")
        self.assertEqual(replay.headers["Retry-After"], "17")
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(limiter.consume.call_count, 1)
        self.assertEqual(len(adapter.calls), 1)

    def test_same_idempotency_key_with_different_brief_is_conflict(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            first = self._post(topic="主题一", request_id="req-one")
            conflict = self._post(topic="主题二", request_id="req-two")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["data"]["error_code"], "idempotency_conflict")
        self.assertEqual(len(adapter.calls), 1)

    def test_read_timeout_is_unknown_and_can_be_queried_without_retry(self):
        adapter = MockContentEngineAdapter(
            [ContentEngineTimeoutError("读取超时，结果未知", trace_id="trace-timeout")]
        )
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            response = self._post(key="timeout-key", request_id="req-timeout")

        self.assertEqual(response.status_code, 504)
        error = response.get_json()["data"]
        self.assertTrue(error["state_unknown"])
        self.assertIsNotNone(error["run_id"])
        run = self.client.get(f"/api/content-generation/runs/{error['run_id']}")
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.get_json()["data"]["status"], "unknown")
        self.assertEqual(len(adapter.calls), 1)

    def test_generation_score_rescore_and_save_share_snapshot_context(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            generated_response = self._post(
                key="score-context",
                request_id="req-score-context",
            )
        self.assertEqual(generated_response.status_code, 200)
        generated = generated_response.get_json()["data"]
        run_id = generated["generation"]["run_id"]

        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET name = ?, keywords = ? WHERE id = ?",
                    (
                        "后来改名",
                        json.dumps(["完全不同关键词"], ensure_ascii=False),
                        self.project_id,
                    ),
                )

        rescored_response = self.client.post(
            "/api/geo/score",
            json={
                "title": generated["title"],
                "content": generated["content"],
                "brand": "客户端伪造品牌",
                "keywords": ["完全不同关键词"],
                "generation_run_id": run_id,
            },
        )
        self.assertEqual(rescored_response.status_code, 200)
        rescored = rescored_response.get_json()
        self.assertEqual(rescored["score_context"]["brand"], "XX科技")
        self.assertEqual(
            rescored["score_context"]["keywords"],
            ["AI Agent", "企业智能体"],
        )

        saved_response = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": generated["title"],
                "summary": generated["summary"],
                "content": generated["content"],
                "tags": generated["tags"],
                "status": "ready",
                "generation_run_id": run_id,
            },
        )
        self.assertEqual(saved_response.status_code, 201)
        saved = saved_response.get_json()["data"]
        self.assertEqual(generated["geo_score"], rescored["score"])
        self.assertEqual(rescored["score"], saved["geo_score"])
        self.assertEqual(saved["generation"]["sources"], VALID_RESULT["sources"])

    def test_optimize_before_and_after_scores_use_generation_snapshot(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            generated = self._post(
                key="optimize-context",
                request_id="req-optimize-context",
            ).get_json()["data"]
        saved = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": generated["title"],
                "summary": generated["summary"],
                "content": generated["content"],
                "tags": generated["tags"],
                "status": "ready",
                "generation_run_id": generated["generation"]["run_id"],
            },
        ).get_json()["data"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET name = ?, keywords = ? WHERE id = ?",
                    ("后来改名", json.dumps(["后来关键词"], ensure_ascii=False), self.project_id),
                )

        score_calls = []

        def fake_score(*, title, content, brand, keywords):
            score_calls.append({"brand": brand, "keywords": list(keywords)})
            return {"score": 77, "dimensions": {}, "suggestions": []}

        with patch("sau_backend.score_geo_content", side_effect=fake_score), patch(
            "sau_backend.optimize_geo_content",
            return_value={
                "title": "优化标题",
                "summary": "优化摘要",
                "content": "优化正文",
                "tags": ["AI Agent"],
                "faq": [],
                "sources": [],
            },
        ), patch("sau_backend._enforce_ai_rate_limit", return_value=None):
            response = self.client.post(f"/api/articles/{saved['id']}/optimize", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(score_calls), 2)
        self.assertEqual(
            score_calls,
            [
                {"brand": "XX科技", "keywords": ["AI Agent", "企业智能体"]},
                {"brand": "XX科技", "keywords": ["AI Agent", "企业智能体"]},
            ],
        )

    def test_only_connection_failure_uses_fallback(self):
        primary = MockContentEngineAdapter(
            [ContentEngineUnavailableError("连接失败")],
            version="primary",
        )
        primary.name = "colleague"
        fallback = MockContentEngineAdapter([VALID_RESULT], version="fallback")
        fallback.name = "qwen"
        service = ContentGenerationService(self.db_path, primary, fallback)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            response = self._post(key="fallback-key", request_id="req-fallback")

        self.assertEqual(response.status_code, 200)
        generation = response.get_json()["data"]["generation"]
        self.assertTrue(generation["fallback_used"])
        self.assertEqual(generation["fallback_from"], "colleague")
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 1)

    def test_protocol_error_does_not_use_fallback(self):
        primary = MockContentEngineAdapter(
            [ContentEngineProtocolError("缺少标题")],
            version="primary",
        )
        primary.name = "colleague"
        fallback = MockContentEngineAdapter([VALID_RESULT], version="fallback")
        fallback.name = "qwen"
        service = ContentGenerationService(self.db_path, primary, fallback)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            response = self._post(key="protocol-key", request_id="req-protocol")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 0)

    def test_engine_status_is_read_only_and_does_not_expose_configuration(self):
        service = Mock()
        service.health.return_value = {
            "status": "degraded",
            "ready": True,
            "primary": {
                "engine": "colleague",
                "version": "2026.09",
                "ready": False,
                "status": "unavailable",
            },
            "fallback": {
                "engine": "qwen",
                "version": "qwen3.8-max",
                "ready": True,
                "status": "ready",
            },
        }
        with patch(
            "sau_backend._create_content_generation_service",
            return_value=service,
        ):
            response = self.client.get("/api/content-engine/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["managed_by"], "deployment_environment")
        serialized = response.get_data(as_text=True)
        self.assertNotIn("api_key", serialized.casefold())
        self.assertNotIn("base_url", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
