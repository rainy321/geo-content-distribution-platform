import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import requests

from db.createTable import initialize_database
from services.ai_service import AISettings
from services.content_engine_adapters import (
    ColleagueContentEngineAdapter,
    MockContentEngineAdapter,
)
from services.content_engine_contract import (
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
    ContentGenerationRequest,
)
from services.content_generation_run_service import (
    GenerationRunStateError,
    IdempotencyConflictError,
    bind_article,
    get_run,
    get_run_by_idempotency_key,
    get_run_by_request_id,
    get_run_score_context,
    begin_or_replay,
)
from services.content_generation_service import ContentGenerationService


def engine_payload(*, title="企业 AI Agent 指南", engine="mock", usage=None):
    return {
        "title": title,
        "summary": "实施摘要",
        "content": (
            "## 实施路径\nXX科技帮助团队建设企业 AI Agent。\n\n"
            "## 常见问题\nQ：如何开始？\nA：先验证场景。\n\n"
            "## 总结\n从小范围验证开始。"
        ),
        "tags": ["AI Agent"],
        "faq": [{"question": "如何开始？", "answer": "先验证场景。"}],
        "sources": [],
        "engine": engine,
        "engine_version": "test-1",
        "usage": usage,
    }


class ContentGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "XX科技",
                        "https://example.test",
                        "企业 AI Agent",
                        "人工智能",
                        "帮助企业构建智能服务流程。",
                        json.dumps(["项目默认关键词"], ensure_ascii=False),
                        "[]",
                    ),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, *, request_id="request-1", idempotency_key="idem-1", topic="测试主题"):
        return ContentGenerationRequest.create(
            project_context={
                "id": self.project_id,
                "name": "XX科技",
                "website": "https://example.test",
                "product": "企业 AI Agent",
                "industry": "人工智能",
                "description": "帮助企业构建智能服务流程。",
                "keywords": ["项目默认关键词"],
            },
            topic=topic,
            keywords=["AI Agent"],
            length=1000,
            content_type="行业科普",
            target_platform="zhihu",
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def test_success_persists_local_score_and_null_usage(self):
        primary = MockContentEngineAdapter([engine_payload(usage=None)])
        result = ContentGenerationService(self.db_path, primary).generate(self.request())

        self.assertIn("geo_score", result)
        self.assertIn("geo_analysis", result)
        self.assertIsNone(result["generation"]["usage"])
        self.assertEqual(result["generation"]["usage_status"], "unknown")
        run = get_run(self.db_path, result["generation"]["run_id"])
        self.assertEqual(run["status"], "succeeded")
        self.assertIsNone(run["usage"])
        self.assertEqual(run["provider_geo_score"], None)

    def test_only_connection_failure_falls_back(self):
        primary = MockContentEngineAdapter(
            [ContentEngineUnavailableError("connection refused")],
            version="primary",
        )
        primary.name = "colleague"
        fallback = MockContentEngineAdapter([engine_payload(engine="qwen")], version="qwen-test")
        fallback.name = "qwen"

        result = ContentGenerationService(self.db_path, primary, fallback).generate(self.request())

        self.assertTrue(result["generation"]["fallback_used"])
        self.assertEqual(result["generation"]["fallback_from"], "colleague")
        self.assertEqual(len(fallback.calls), 1)
        run = get_run(self.db_path, result["generation"]["run_id"])
        self.assertTrue(run["fallback_used"])
        self.assertEqual(run["actual_engine"], "qwen")

    def test_read_timeout_is_unknown_and_does_not_fallback(self):
        primary = MockContentEngineAdapter(
            [ContentEngineTimeoutError("read timeout", trace_id="trace-unknown")]
        )
        primary.name = "colleague"
        fallback = MockContentEngineAdapter([engine_payload(engine="qwen")])
        fallback.name = "qwen"

        with self.assertRaises(ContentEngineTimeoutError) as raised:
            ContentGenerationService(self.db_path, primary, fallback).generate(self.request())

        self.assertEqual(fallback.calls, [])
        self.assertEqual(raised.exception.request_id, "request-1")
        self.assertIsInstance(raised.exception.run_id, int)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT id, status, error_code FROM content_generation_runs"
            ).fetchone()
        self.assertEqual(row[1:], ("unknown", "engine_timeout_unknown"))
        self.assertEqual(
            get_run_by_request_id(self.db_path, "request-1")["id"],
            raised.exception.run_id,
        )
        self.assertEqual(
            get_run_by_idempotency_key(self.db_path, "idem-1")["id"],
            raised.exception.run_id,
        )

    def test_ambiguous_transport_disconnect_is_unknown_and_does_not_fallback(self):
        def disconnect_after_send(*args, **kwargs):
            raise requests.ConnectionError("connection reset")

        primary = ColleagueContentEngineAdapter(
            base_url="http://content-engine:8080",
            token="internal-secret",
            http_post=disconnect_after_send,
        )
        fallback = MockContentEngineAdapter([engine_payload(engine="qwen")])
        fallback.name = "qwen"

        with self.assertRaises(ContentEngineTimeoutError) as raised:
            ContentGenerationService(self.db_path, primary, fallback).generate(
                self.request()
            )

        self.assertEqual(fallback.calls, [])
        self.assertEqual(get_run(self.db_path, raised.exception.run_id)["status"], "unknown")

    def test_idempotent_success_is_replayed_without_second_engine_call(self):
        primary = MockContentEngineAdapter([engine_payload()])
        service = ContentGenerationService(self.db_path, primary)
        first = service.generate(self.request(request_id="request-a"))
        second = service.generate(self.request(request_id="request-b"))

        self.assertFalse(first["generation"]["replayed"])
        self.assertTrue(second["generation"]["replayed"])
        first_without_replay = json.loads(json.dumps(first, ensure_ascii=False))
        second_without_replay = json.loads(json.dumps(second, ensure_ascii=False))
        first_without_replay["generation"].pop("replayed")
        second_without_replay["generation"].pop("replayed")
        self.assertEqual(first_without_replay, second_without_replay)
        self.assertEqual(len(primary.calls), 1)

    def test_reusing_idempotency_key_for_different_brief_conflicts(self):
        primary = MockContentEngineAdapter([engine_payload()])
        service = ContentGenerationService(self.db_path, primary)
        service.generate(self.request(request_id="request-a"))

        with self.assertRaises(IdempotencyConflictError) as raised:
            service.generate(
                self.request(request_id="request-b", topic="不同主题")
            )
        self.assertIsInstance(raised.exception.run_id, int)
        self.assertEqual(raised.exception.request_id, "request-b")

    def test_custom_ai_settings_force_qwen_primary_without_fallback(self):
        settings = AISettings(
            base_url="https://api.example.test/v1",
            api_key="browser-secret",
            model="custom-model",
        )
        with patch.dict(
            "os.environ",
            {
                "CONTENT_ENGINE": "colleague",
                "CONTENT_ENGINE_FALLBACK": "qwen",
            },
            clear=True,
        ):
            service = ContentGenerationService.from_environment(
                self.db_path,
                qwen_settings=settings,
            )

        self.assertEqual(service.primary_adapter.name, "qwen")
        self.assertIsNone(service.fallback_adapter)
        serialized_health = json.dumps(service.health(), ensure_ascii=False)
        self.assertNotIn("browser-secret", serialized_health)
        self.assertNotIn("api.example.test", serialized_health)

    def test_post_provider_scoring_failure_marks_run_unknown(self):
        service = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([engine_payload()]),
        )

        with patch(
            "services.content_generation_service.score_geo_content",
            side_effect=RuntimeError("local scorer failed"),
        ), self.assertRaisesRegex(Exception, "结果处理状态未知") as raised:
            service.generate(self.request())

        run = get_run(self.db_path, raised.exception.run_id)
        self.assertEqual(run["status"], "unknown")
        self.assertEqual(run["error_code"], "generation_result_state_unknown")

    def test_abandoned_started_run_ages_into_unknown_without_engine_retry(self):
        request = self.request()
        reserved = begin_or_replay(
            self.db_path,
            request,
            configured_engine="mock",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE content_generation_runs
                    SET updated_at = datetime('now', '-10 minutes')
                    WHERE id = ?
                    """,
                    (reserved.run_id,),
                )

        replay = begin_or_replay(
            self.db_path,
            request,
            configured_engine="mock",
            started_stale_after_seconds=30,
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.status, "unknown")
        self.assertEqual(replay.error_code, "generation_state_unknown")

    def test_bind_article_is_one_to_one_and_uses_snapshot_keywords(self):
        result = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([engine_payload()]),
        ).generate(self.request())
        run_id = result["generation"]["run_id"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, 'draft')
                    """,
                    (self.project_id, "文章", "正文"),
                ).lastrowid
                another_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, 'draft')
                    """,
                    (self.project_id, "另一篇", "正文"),
                ).lastrowid

        bound = bind_article(self.db_path, run_id, article_id)
        self.assertEqual(bound["article_id"], article_id)
        self.assertEqual(bind_article(self.db_path, run_id, article_id)["article_id"], article_id)
        with self.assertRaises(GenerationRunStateError):
            bind_article(self.db_path, run_id, another_id)
        self.assertEqual(get_run_score_context(self.db_path, run_id)["keywords"], ["AI Agent"])


class ConcurrentDatabaseInitializationTests(unittest.TestCase):
    def test_schema_initialization_is_safe_when_web_and_worker_start_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "database.db"
            with ThreadPoolExecutor(max_workers=6) as executor:
                paths = list(executor.map(initialize_database, [database_path] * 6))
            self.assertEqual(paths, [database_path.resolve()] * 6)
            with closing(sqlite3.connect(database_path)) as conn:
                table = conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'content_generation_runs'
                    """
                ).fetchone()
            self.assertEqual(table, ("content_generation_runs",))


if __name__ == "__main__":
    unittest.main()
