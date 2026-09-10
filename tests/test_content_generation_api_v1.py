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
    ContentGenerationRequest,
    ContentEngineProtocolError,
    ContentEngineRateLimitError,
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
)
from services.content_generation_run_service import begin_or_replay
from services.content_generation_service import ContentGenerationService
from services.content_hygiene_service import authoring_visible_length


VALID_RESULT = {
    "title": "企业 AI Agent 实践指南",
    "summary": "一份可核验的实施指南。",
    "content": (
        "XX科技围绕 AI Agent 给出实施方法。\n\n"
        "## 实施步骤\n2026 年建议分 3 个阶段验证。\n\n"
        "## FAQ\n问：如何开始？\n答：先验证最小流程。\n"
        "问：如何验收？\n答：保留运行记录。\n\n"
        "## 总结\n以可追踪结果为准。来源：https://example.com/report"
    ),
    "tags": ["AI Agent"],
    "faq": [{"question": "如何开始？", "answer": "先验证最小流程。"}],
    "sources": [{"title": "测试来源", "url": "https://example.com/report"}],
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

    def _post(
        self,
        *,
        topic="AI Agent 落地",
        key="contract-test-key",
        request_id="req-1",
        target_platform="知乎",
        length=1000,
    ):
        return self.client.post(
            "/api/articles/generate",
            headers={"Idempotency-Key": key, "X-Request-ID": request_id},
            json={
                "project_id": self.project_id,
                "topic": topic,
                "keywords": ["AI Agent", "企业智能体"],
                "target_platform": target_platform,
                "length": length,
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
        self.assertEqual(run["score_context"]["target_length"], 1000)
        self.assertEqual(run["score_context"]["requested_target_length"], 1000)
        self.assertEqual(run["score_context"]["effective_target_length"], 1000)
        self.assertEqual(run["score_context"]["length_source"], "requested_length")

    def test_request_identity_lookup_restores_result_without_provider_replay(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            generated = self._post(
                key="refresh-recovery-key",
                request_id="refresh-recovery-request",
                target_platform="知乎",
                length=600,
            )

        self.assertEqual(generated.status_code, 200)
        with patch(
            "sau_backend._create_content_generation_service",
            side_effect=AssertionError("read-only recovery must not create a provider"),
        ):
            recovered = self.client.get(
                "/api/content-generation/runs/by-request/refresh-recovery-request"
            )

        self.assertEqual(recovered.status_code, 200)
        data = recovered.get_json()["data"]
        self.assertEqual(data["status"], "succeeded")
        self.assertEqual(data["request_id"], "refresh-recovery-request")
        self.assertEqual(data["result"]["title"], VALID_RESULT["title"])
        self.assertEqual(data["result"]["content"], VALID_RESULT["content"])
        self.assertEqual(data["brief"]["project_id"], self.project_id)
        self.assertEqual(data["brief"]["topic"], "AI Agent 落地")
        self.assertEqual(data["brief"]["requested_length"], 600)
        self.assertEqual(data["brief"]["target_platform"], "zhihu")
        self.assertNotIn("idempotency_key", data)
        self.assertEqual(len(adapter.calls), 1)

    def test_request_identity_lookup_rejects_invalid_or_missing_identity(self):
        invalid = self.client.get("/api/content-generation/runs/by-request/bad%20request")
        missing = self.client.get(
            "/api/content-generation/runs/by-request/does-not-exist"
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(missing.status_code, 404)

    def test_request_identity_lookup_observes_started_run_without_provider_call(self):
        request = ContentGenerationRequest.create(
            project_context={"id": self.project_id, "name": "XX科技"},
            topic="刷新后等待原结果",
            keywords=["AI Agent"],
            length=200,
            content_type="行业科普",
            target_platform="zhihu",
            request_id="started-refresh-request",
            idempotency_key="started-refresh-key",
        )
        outcome = begin_or_replay(
            self.db_path,
            request,
            configured_engine="mock",
        )

        with patch(
            "sau_backend._create_content_generation_service",
            side_effect=AssertionError("status polling must not create a provider"),
        ):
            response = self.client.get(
                "/api/content-generation/runs/by-request/started-refresh-request"
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["id"], outcome.run_id)
        self.assertEqual(data["status"], "started")
        self.assertIsNone(data["result"])
        self.assertEqual(data["brief"]["topic"], "刷新后等待原结果")

    def test_platform_length_context_survives_run_and_explicit_rescore_respects_cap(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            generated_response = self._post(
                key="kuaishou-length-context",
                request_id="req-kuaishou-length-context",
                target_platform="快手",
                length=1000,
            )

        self.assertEqual(generated_response.status_code, 200)
        generated = generated_response.get_json()["data"]
        generation_profile = generated["geo_analysis"]["length_profile"]
        self.assertEqual(generation_profile["target_length"], 1000)
        self.assertEqual(generation_profile["requested_target_length"], 1000)
        self.assertEqual(generation_profile["effective_target_length"], 380)
        self.assertEqual(generation_profile["source"], "platform_safe_limit")
        self.assertEqual(generation_profile["key"], "compact")

        run_id = generated["generation"]["run_id"]
        run = self.client.get(
            f"/api/content-generation/runs/{run_id}"
        ).get_json()["data"]
        self.assertEqual(run["score_context"]["target_length"], 380)
        self.assertEqual(run["score_context"]["requested_target_length"], 1000)
        self.assertEqual(run["score_context"]["effective_target_length"], 380)
        self.assertEqual(run["score_context"]["platform_limit"], 380)
        self.assertEqual(run["score_context"]["length_source"], "platform_safe_limit")

        capped_rescore = self.client.post(
            "/api/geo/score",
            json={
                "title": generated["title"],
                "summary": generated["summary"],
                "content": generated["content"],
                "generation_run_id": run_id,
                "target_length": 1000,
            },
        )
        self.assertEqual(capped_rescore.status_code, 200)
        capped_payload = capped_rescore.get_json()
        self.assertEqual(capped_payload["length_profile"]["target_length"], 1000)
        self.assertEqual(
            capped_payload["length_profile"]["requested_target_length"],
            1000,
        )
        self.assertEqual(
            capped_payload["length_profile"]["effective_target_length"],
            380,
        )
        self.assertEqual(
            capped_payload["length_profile"]["source"],
            "platform_safe_limit",
        )
        self.assertEqual(capped_payload["score_context"]["target_length"], 380)
        self.assertEqual(
            capped_payload["score_context"]["requested_target_length"],
            1000,
        )
        self.assertEqual(
            capped_payload["score_context"]["effective_target_length"],
            380,
        )
        self.assertEqual(capped_payload["score_context"]["platform_limit"], 380)
        self.assertEqual(
            capped_payload["score_context"]["length_source"],
            "platform_safe_limit",
        )

        rescored = self.client.post(
            "/api/geo/score",
            json={
                "title": generated["title"],
                "summary": generated["summary"],
                "content": generated["content"],
                "generation_run_id": run_id,
                "target_length": 200,
            },
        )
        self.assertEqual(rescored.status_code, 200)
        rescored_payload = rescored.get_json()
        self.assertEqual(rescored_payload["length_profile"]["target_length"], 200)
        self.assertEqual(
            rescored_payload["length_profile"]["effective_target_length"],
            200,
        )
        self.assertEqual(rescored_payload["length_profile"]["source"], "explicit")
        self.assertEqual(rescored_payload["score_context"]["target_length"], 200)
        self.assertEqual(
            rescored_payload["score_context"]["requested_target_length"],
            200,
        )
        self.assertEqual(rescored_payload["score_context"]["length_source"], "explicit")

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
        self.assertEqual(
            rescored["citeability"],
            generated["geo_analysis"]["citeability"],
        )
        self.assertEqual(
            rescored["citeability"]["signals"]["citation_count"],
            1,
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
        self.assertEqual(
            saved["geo_analysis"]["citeability"],
            generated["geo_analysis"]["citeability"],
        )
        self.assertEqual(saved["generation"]["sources"], VALID_RESULT["sources"])

    def test_legacy_generation_receipt_cannot_accept_client_supplied_sources(self):
        adapter = MockContentEngineAdapter([VALID_RESULT], version="1.0-test")
        service = ContentGenerationService(self.db_path, adapter)
        with patch("sau_backend._create_content_generation_service", return_value=service):
            generated = self._post(
                key="legacy-source-context",
                request_id="req-legacy-source-context",
            ).get_json()["data"]
        run_id = generated["generation"]["run_id"]

        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT result_payload FROM content_generation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            payload = json.loads(row[0])
            payload.pop("sources", None)
            with conn:
                conn.execute(
                    "UPDATE content_generation_runs SET result_payload = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), run_id),
                )

        response = self.client.post(
            "/api/geo/score",
            json={
                "title": generated["title"],
                "summary": generated["summary"],
                "content": generated["content"].replace(
                    "来源：https://example.com/report", ""
                ),
                "generation_run_id": run_id,
                "sources": [
                    {
                        "title": "客户端伪造来源",
                        "url": "https://untrusted.example.com/client-source",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["citeability"]["signals"]["citation_count"],
            0,
        )

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

        def fake_score(
            *,
            title,
            content,
            brand,
            keywords,
            target_length=None,
            requested_target_length=None,
            source=None,
        ):
            score_calls.append(
                {
                    "brand": brand,
                    "keywords": list(keywords),
                    "target_length": target_length,
                    "requested_target_length": requested_target_length,
                    "source": source,
                }
            )
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
                {
                    "brand": "XX科技",
                    "keywords": ["AI Agent", "企业智能体"],
                    "target_length": 1000,
                    "requested_target_length": 1000,
                    "source": "requested_length",
                },
                {
                    "brand": "XX科技",
                    "keywords": ["AI Agent", "企业智能体"],
                    "target_length": 1000,
                    "requested_target_length": 1000,
                    "source": "requested_length",
                },
            ],
        )

    def test_optimize_sanitizes_placeholders_and_enforces_saved_target_length(self):
        saved = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "AI Agent 是什么？",
                "summary": "用于验证短稿优化边界。",
                "content": "XX科技说明 AI Agent 的适用条件。\n\n结论以实际测试为准。",
                "tags": ["AI Agent"],
                "status": "ready",
                "target_length": 200,
            },
        ).get_json()["data"]
        overlong_content = (
            "XX科技说明 AI Agent 的实际适用条件、接口边界和验证方法。"
            * 30
            + "\n\n作者：[待补充]\n更新时间：[待补充]"
        )

        with patch(
            "sau_backend.optimize_geo_content",
            return_value={
                "title": "AI Agent 是什么？",
                "summary": "用于验证短稿优化边界。",
                "content": overlong_content,
                "tags": ["AI Agent"],
                "faq": [],
                "sources": [],
            },
        ), patch("sau_backend._enforce_ai_rate_limit", return_value=None):
            response = self.client.post(f"/api/articles/{saved['id']}/optimize", json={})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        optimized_content = data["optimized"]["content"]
        warning_codes = {item["code"] for item in data["warnings"]}
        self.assertLessEqual(authoring_visible_length(optimized_content), 200)
        self.assertNotIn("待补充", optimized_content)
        self.assertIn("optimization_placeholders_removed", warning_codes)
        self.assertIn("optimization_length_constrained", warning_codes)
        self.assertEqual(data["after"]["length_profile"]["target_length"], 200)

    def test_optimize_restores_configured_https_owned_source(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET website = ? WHERE id = ?",
                    ("https://example.com/product", self.project_id),
                )
        saved = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "AI Agent 适合谁？",
                "summary": "受控测试。",
                "content": "XX科技的 AI Agent 适合先验证小范围流程的团队。",
                "tags": ["AI Agent"],
                "status": "ready",
                "target_length": 200,
            },
        ).get_json()["data"]

        with patch(
            "sau_backend.optimize_geo_content",
            return_value={
                "title": "AI Agent 适合谁？",
                "summary": "受控测试。",
                "content": "XX科技的 AI Agent 适合先验证小范围流程的团队。",
                "tags": ["AI Agent"],
                "faq": [],
                "sources": [],
            },
        ), patch("sau_backend._enforce_ai_rate_limit", return_value=None):
            response = self.client.post(f"/api/articles/{saved['id']}/optimize", json={})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertIn("https://example.com/product", data["optimized"]["content"])
        self.assertIn(
            "optimization_owned_website_attached",
            {item["code"] for item in data["warnings"]},
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
