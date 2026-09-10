import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from db.createTable import initialize_database
from services.ai_service import (
    AIGenerationResult,
    AIOptimizationResult,
    AIServiceError,
    AISettings,
)
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
from services.content_hygiene_service import authoring_visible_length


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

    def request(
        self,
        *,
        request_id="request-1",
        idempotency_key="idem-1",
        topic="测试主题",
        length=1000,
        target_platform="zhihu",
        website="https://example.test",
    ):
        return ContentGenerationRequest.create(
            project_context={
                "id": self.project_id,
                "name": "XX科技",
                "website": website,
                "product": "企业 AI Agent",
                "industry": "人工智能",
                "description": "帮助企业构建智能服务流程。",
                "keywords": ["项目默认关键词"],
            },
            topic=topic,
            keywords=["AI Agent"],
            length=length,
            content_type="行业科普",
            target_platform=target_platform,
            ai_role={
                "role": "writer",
                "configured": True,
                "fallback": False,
                "source_role": "writer",
            },
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def test_success_persists_local_score_and_null_usage(self):
        primary = MockContentEngineAdapter([engine_payload(usage=None)])
        result = ContentGenerationService(self.db_path, primary).generate(self.request())

        self.assertIn("geo_score", result)
        self.assertIn("geo_analysis", result)
        self.assertEqual(
            result["geo_analysis"]["citeability"]["version"],
            "ai-citeability-rules-2.2.0",
        )
        self.assertEqual(
            len(result["geo_analysis"]["citeability"]["dimensions"]),
            7,
        )
        self.assertIsNone(result["generation"]["usage"])
        self.assertEqual(result["generation"]["usage_status"], "unknown")
        self.assertEqual(result["generation"]["ai_role"]["role"], "writer")
        self.assertFalse(result["generation"]["ai_role"]["fallback"])
        self.assertIn("https://example.test", result["content"])
        self.assertEqual(
            result["sources"],
            [{"title": "XX科技官网", "url": "https://example.test"}],
        )
        self.assertTrue(
            any("不代表第三方背书" in item for item in result["generation"]["warnings"])
        )
        run = get_run(self.db_path, result["generation"]["run_id"])
        self.assertEqual(run["status"], "succeeded")
        self.assertIsNone(run["usage"])
        self.assertEqual(run["provider_geo_score"], None)
        self.assertEqual(
            run["result_payload"]["generation"]["ai_role"]["source_role"],
            "writer",
        )

    def test_openai_compatible_success_persists_actual_provider_to_run_and_ledger(self):
        settings = AISettings(
            provider="doubao",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="synthetic-secret",
            model="doubao-writer-model",
        )
        generated = AIGenerationResult(
            article=engine_payload(
                title="豆包生成结果",
                usage={"input_tokens": 30, "output_tokens": 20, "total_tokens": 50},
            ),
            model="doubao-writer-model-202609",
            provider_request_id="doubao-request-success",
            elapsed_ms=4,
            usage={"input_tokens": 30, "output_tokens": 20, "total_tokens": 50},
        )
        adapter = QwenContentEngineAdapter(settings=settings)

        with patch(
            "services.content_engine_adapters.generate_geo_content_detailed",
            return_value=generated,
        ):
            result = ContentGenerationService(self.db_path, adapter).generate(
                self.request(request_id="doubao-success", idempotency_key="doubao-success-key")
            )

        self.assertEqual(adapter.name, "qwen")
        self.assertEqual(adapter.provider_name, "doubao")
        self.assertEqual(result["generation"]["engine"], "doubao")
        run = get_run(self.db_path, result["generation"]["run_id"])
        self.assertEqual(run["configured_engine"], "qwen")
        self.assertEqual(run["actual_engine"], "doubao")
        with closing(sqlite3.connect(self.db_path)) as conn:
            receipt = conn.execute(
                """
                SELECT provider, model, usage_status, total_tokens
                FROM ai_usage_ledger WHERE operation = 'generate'
                """
            ).fetchone()
        self.assertEqual(
            receipt,
            ("doubao", "doubao-writer-model-202609", "reported", 50),
        )

    def test_openai_compatible_timeout_persists_actual_provider_to_unknown_receipt(self):
        def read_timeout(*args, **kwargs):
            raise requests.ReadTimeout("synthetic timeout")

        adapter = QwenContentEngineAdapter(
            settings=AISettings(
                provider="doubao",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="synthetic-secret",
                model="doubao-writer-model",
            ),
            http_post=read_timeout,
        )

        with self.assertRaises(ContentEngineTimeoutError) as raised:
            ContentGenerationService(self.db_path, adapter).generate(
                self.request(
                    request_id="doubao-timeout",
                    idempotency_key="doubao-timeout-key",
                    website="https://www.example.com",
                )
            )

        run = get_run(self.db_path, raised.exception.run_id)
        self.assertEqual(run["configured_engine"], "qwen")
        self.assertEqual(run["actual_engine"], "doubao")
        self.assertEqual(run["status"], "unknown")
        self.assertEqual(raised.exception.attempted_engine, "doubao")
        with closing(sqlite3.connect(self.db_path)) as conn:
            receipt = conn.execute(
                """
                SELECT provider, model, usage_status, total_tokens, cost_status
                FROM ai_usage_ledger WHERE operation = 'generate'
                """
            ).fetchone()
        self.assertEqual(
            receipt,
            ("doubao", "unknown", "unknown", None, "unknown_usage"),
        )

    def test_unconfigured_reviewer_is_explicit_writer_only_without_fake_receipt(self):
        result = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([engine_payload(usage={"total_tokens": 12})]),
        ).generate(self.request())

        review = result["generation"]["review"]
        self.assertFalse(review["configured"])
        self.assertTrue(review["fallback"])
        self.assertEqual(review["source_role"], "writer")
        self.assertEqual(review["mode"], "writer_only")
        self.assertFalse(review["invoked"])
        self.assertEqual(review["selected"], "writer")
        self.assertEqual(review["usage_status"], "not_invoked")
        self.assertIsNone(review["comparison"])
        self.assertIsNone(review["provider"])
        self.assertIsNone(review["model"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            operations = conn.execute(
                "SELECT operation FROM ai_usage_ledger ORDER BY id"
            ).fetchall()
        self.assertEqual(operations, [("generate",)])

    def test_configured_reviewer_optimizes_compares_and_records_both_costs_once(self):
        writer_payload = engine_payload(
            title="Writer title",
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
        reviewer = Mock(
            return_value=AIOptimizationResult(
                article={
                    "title": "Reviewer title",
                    "summary": "Reviewer summary",
                    "content": (
                        "## 直接答案\nXX科技的企业 AI Agent 适用于受控流程。\n\n"
                        "## 验证边界\n结论需要用输入资料和人工复核验证。"
                    ),
                    "tags": ["AI Agent", "受控流程"],
                    "faq": [
                        {"question": "如何验证？", "answer": "先在小范围流程中测试。"}
                    ],
                },
                provider="deepseek",
                model="review-model",
                provider_request_id="review-provider-request-1",
                elapsed_ms=23,
                usage={"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
            )
        )
        settings = AISettings(
            provider="deepseek",
            base_url="https://review.example.test/v1",
            api_key="review-secret",
            model="review-model",
        )
        service = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([writer_payload]),
            reviewer_settings=settings,
            reviewer_role={
                "role": "reviewer",
                "configured": True,
                "effective_configured": True,
                "fallback": False,
                "source_role": "reviewer",
                "same_as_writer": False,
            },
            reviewer_optimizer=reviewer,
        )
        writer_geo = {
            "score": 60,
            "dimensions": {"entity": 60},
            "suggestions": ["writer suggestion"],
            "length_profile": {"key": "standard"},
        }
        reviewer_geo = {
            "score": 82,
            "dimensions": {"entity": 100},
            "suggestions": [],
            "length_profile": {"key": "standard"},
        }
        writer_citeability = {
            "score": 64,
            "publish_gate_passed": True,
            "dimensions": {},
        }
        reviewer_citeability = {
            "score": 88,
            "publish_gate_passed": True,
            "dimensions": {},
        }
        price_catalog = json.dumps(
            {
                "mock/test-1": {
                    "input_per_million": "2",
                    "output_per_million": "6",
                    "currency": "CNY",
                },
                "deepseek/review-model": {
                    "input_per_million": "3",
                    "output_per_million": "9",
                    "currency": "CNY",
                },
            }
        )

        with patch.dict(
            "os.environ",
            {"AI_PRICE_CATALOG_JSON": price_catalog},
            clear=False,
        ), patch(
            "services.content_generation_service.score_geo_content",
            side_effect=[writer_geo, reviewer_geo],
        ), patch(
            "services.content_generation_service.score_ai_citeability",
            side_effect=[writer_citeability, reviewer_citeability],
        ):
            first = service.generate(self.request(request_id="review-request-1"))
            replay = service.generate(self.request(request_id="review-request-2"))

        self.assertEqual(first["title"], "Reviewer title")
        self.assertEqual(first["geo_score"], 82)
        review = first["generation"]["review"]
        self.assertTrue(review["configured"])
        self.assertTrue(review["invoked"])
        self.assertTrue(review["completed"])
        self.assertEqual(review["selected"], "reviewer")
        self.assertEqual(review["provider"], "deepseek")
        self.assertEqual(review["model"], "review-model")
        self.assertEqual(review["usage"]["total_tokens"], 60)
        self.assertEqual(
            review["comparison"]["method"],
            "deterministic_local_quality_v1",
        )
        self.assertEqual(review["comparison"]["writer"]["geo_score"], 60)
        self.assertEqual(review["comparison"]["reviewer"]["geo_score"], 82)
        self.assertTrue(replay["generation"]["replayed"])
        self.assertEqual(replay["title"], "Reviewer title")
        reviewer.assert_called_once()
        self.assertEqual(reviewer.call_args.kwargs["settings"].model, "review-model")
        self.assertTrue(reviewer.call_args.kwargs["include_metadata"])
        self.assertEqual(reviewer.call_args.kwargs["target_length"], 1000)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            receipts = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT operation, provider, model, usage_status,
                           total_tokens, estimated_cost, cost_status,
                           reference_type, reference_id
                    FROM ai_usage_ledger
                    ORDER BY id
                    """
                ).fetchall()
            ]
        self.assertEqual([item["operation"] for item in receipts], ["generate", "optimize"])
        self.assertEqual([item["total_tokens"] for item in receipts], [150, 60])
        self.assertEqual([item["cost_status"] for item in receipts], ["calculated", "calculated"])
        self.assertEqual(receipts[0]["estimated_cost"], "0.0005")
        self.assertEqual(receipts[1]["estimated_cost"], "0.0003")
        self.assertEqual(receipts[1]["provider"], "deepseek")
        self.assertEqual(receipts[1]["model"], "review-model")
        self.assertEqual(receipts[0]["reference_type"], "generation_run")
        self.assertEqual(receipts[0]["reference_id"], receipts[1]["reference_id"])

    def test_reviewer_failure_keeps_writer_and_records_unknown_attempt(self):
        writer_payload = engine_payload(title="Writer survives")
        reviewer = Mock(side_effect=AIServiceError("synthetic reviewer failure"))
        service = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([writer_payload]),
            reviewer_settings=AISettings(
                provider="deepseek",
                base_url="https://review.example.test/v1",
                api_key="review-secret",
                model="review-model",
            ),
            reviewer_role={
                "configured": True,
                "effective_configured": True,
                "fallback": False,
                "source_role": "reviewer",
            },
            reviewer_optimizer=reviewer,
        )

        result = service.generate(self.request())

        self.assertEqual(result["title"], "Writer survives")
        review = result["generation"]["review"]
        self.assertTrue(review["invoked"])
        self.assertFalse(review["completed"])
        self.assertTrue(review["writer_fallback_used"])
        self.assertEqual(review["reason"], "reviewer_call_failed")
        self.assertIsNone(review["comparison"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            receipt = conn.execute(
                """
                SELECT usage_status, total_tokens, estimated_cost, cost_status
                FROM ai_usage_ledger WHERE operation = 'optimize'
                """
            ).fetchone()
        self.assertEqual(receipt, ("unknown", None, None, "unknown_usage"))

    def test_platform_safe_length_drives_cleanup_scoring_and_run_context(self):
        payload = engine_payload()
        payload["content"] = (
            "XX科技围绕 AI Agent 说明适用条件、实现机制和可验证边界。"
            * 80
        )
        result = ContentGenerationService(
            self.db_path,
            MockContentEngineAdapter([payload]),
        ).generate(
            self.request(
                request_id="request-kuaishou-length",
                idempotency_key="idem-kuaishou-length",
                length=1000,
                target_platform="快手",
            )
        )

        self.assertLessEqual(authoring_visible_length(result["content"]), 380)
        self.assertTrue(
            any(
                "原始请求 1000 字" in warning and "380 字平台安全线" in warning
                for warning in result["generation"]["warnings"]
            )
        )
        profile = result["geo_analysis"]["length_profile"]
        self.assertEqual(profile["target_length"], 1000)
        self.assertEqual(profile["requested_target_length"], 1000)
        self.assertEqual(profile["effective_target_length"], 380)
        self.assertEqual(profile["source"], "platform_safe_limit")
        self.assertEqual(profile["key"], "compact")

        context = get_run_score_context(
            self.db_path,
            result["generation"]["run_id"],
        )
        self.assertEqual(context["target_length"], 380)
        self.assertEqual(context["requested_target_length"], 1000)
        self.assertEqual(context["effective_target_length"], 380)
        self.assertEqual(context["platform_limit"], 380)
        self.assertEqual(context["length_source"], "platform_safe_limit")

    def test_success_removes_unfinished_placeholders_before_persistence(self):
        payload = engine_payload()
        payload["content"] += (
            "\n\n作者：[作者占位]\n"
            "更新时间：[待补充]\n\n"
            "## 参考资料\n"
            "- 官方产品页：https://example.org/product\n"
            "- 行业规范（待补充证据）"
        )
        primary = MockContentEngineAdapter([payload])

        result = ContentGenerationService(self.db_path, primary).generate(self.request())

        self.assertNotRegex(result["content"], "作者占位|待补充")
        self.assertIn("https://example.org/product", result["content"])
        self.assertIn(
            "已移除未完成的作者、日期或证据占位标记",
            result["generation"]["warnings"],
        )
        persisted = get_run(self.db_path, result["generation"]["run_id"])
        self.assertNotRegex(persisted["result_payload"]["content"], "作者占位|待补充")

    def test_oversized_model_output_is_compressed_before_scoring_and_persistence(self):
        payload = engine_payload()
        payload["content"] = (
            "XX科技的企业 AI Agent 用于验证业务流程。" * 25
            + "\n\n限制与风险：结果取决于输入资料并需要人工复核。"
        )
        original_length = authoring_visible_length(payload["content"])
        primary = MockContentEngineAdapter([payload])

        result = ContentGenerationService(self.db_path, primary).generate(
            self.request(length=200)
        )

        self.assertGreater(original_length, 200)
        self.assertLessEqual(authoring_visible_length(result["content"]), 200)
        self.assertIn("限制与风险", result["content"])
        self.assertIn("https://example.test", result["content"])
        warning = next(
            item
            for item in result["generation"]["warnings"]
            if "自动压缩" in item
        )
        self.assertIn("→ 200 字", warning)
        persisted = get_run(self.db_path, result["generation"]["run_id"])
        self.assertEqual(persisted["result_payload"]["content"], result["content"])

    def test_under_target_output_warns_without_inventing_padding(self):
        payload = engine_payload()
        payload["content"] = "## 标题\n\n" + ("字 " * 140)
        self.assertGreater(len(payload["content"]), 200)
        self.assertEqual(authoring_visible_length(payload["content"]), 144)
        primary = MockContentEngineAdapter([payload])

        result = ContentGenerationService(self.db_path, primary).generate(
            self.request(length=200, website="")
        )

        self.assertEqual(result["content"], payload["content"].strip())
        self.assertFalse(
            any("自动压缩" in item for item in result["generation"]["warnings"])
        )
        warning = next(
            item
            for item in result["generation"]["warnings"]
            if "可见字数低于目标合理下界" in item
        )
        self.assertIn("144 / 200", warning)
        self.assertIn("建议至少 160 字", warning)
        self.assertIn("未自动补字", warning)

    def test_content_inside_requested_length_is_not_reported_as_compressed(self):
        primary = MockContentEngineAdapter([engine_payload()])
        result = ContentGenerationService(self.db_path, primary).generate(
            self.request(length=200)
        )

        self.assertFalse(
            any("自动压缩" in item for item in result["generation"]["warnings"])
        )

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
