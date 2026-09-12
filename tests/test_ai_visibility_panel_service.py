import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.ai_service import AICompletionResult, AISettings
from services.ai_visibility_panel_service import (
    PANEL_IN_PROGRESS_EXPIRY_SECONDS,
    VisibilityPanelConflictError,
    VisibilityPanelInProgressError,
    VisibilityPanelValidationError,
    get_ai_visibility_panel_request,
    run_ai_visibility_panel,
)
from services.ai_visibility_sampling_service import run_ai_visibility_sample


class AIVisibilityPanelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "panel.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                self.project_id = int(
                    conn.execute(
                        """
                        INSERT INTO projects (
                            name, website, product, industry, description,
                            keywords, competitors
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "XX科技",
                            "https://example.test",
                            "AI Agent",
                            "AI 软件",
                            "本地合成测试",
                            "[]",
                            json.dumps(["YY科技"], ensure_ascii=False),
                        ),
                    ).lastrowid
                )
        self.project = {
            "id": self.project_id,
            "name": "XX科技",
            "website": "https://example.test",
            "product": "AI Agent",
            "keywords": [],
            "competitors": ["YY科技"],
        }
        self.writer = AISettings(
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="writer-secret",
            model="writer-model",
        )
        self.reviewer = AISettings(
            provider="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key="review-secret",
            model="review-model",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def targets(self):
        return [
            {
                "role": "writer",
                "role_status": {"configured": True, "fallback": False},
                "settings": self.writer,
            },
            {
                "role": "reviewer",
                "role_status": {"configured": True, "fallback": False},
                "settings": self.reviewer,
            },
            {
                "role": "evaluator",
                "role_status": {"configured": True, "fallback": False},
                "settings": self.writer,
            },
        ]

    def run_panel(self, runner, **overrides):
        values = {
            "project": self.project,
            "prompt": "哪些 AI Agent 值得考虑？",
            "sample_count": 1,
            "aliases": ["XX科技"],
            "competitors": ["YY科技"],
            "persist": False,
            "confirm_external_call": True,
            "targets": self.targets(),
            "request_id": "panel-request",
            "idempotency_key": "panel-key",
            "runner": runner,
        }
        values.update(overrides)
        return run_ai_visibility_panel(self.database_path, **values)

    def test_parallel_distinct_models_are_durable_and_replay_without_new_charge(self):
        calls = []
        calls_lock = threading.Lock()

        def sampler(*, prompt, settings):
            with calls_lock:
                calls.append((settings.provider, settings.model, prompt))
                call_index = len(calls)
            return AICompletionResult(
                content="XX科技的 AI Agent 可作为选项。 https://example.test",
                model=settings.model,
                provider_request_id=f"provider-{call_index}",
                elapsed_ms=1,
                usage={"total_tokens": 9},
            )

        def durable_runner(database_path, **kwargs):
            return run_ai_visibility_sample(database_path, sampler=sampler, **kwargs)

        first = self.run_panel(durable_runner)
        replay = self.run_panel(durable_runner)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["distinct_model_count"], 2)
        self.assertTrue(first["multi_model_executed"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [item["execution_status"] for item in first["targets"]],
            ["executed", "executed", "skipped"],
        )
        self.assertEqual(first["targets"][2]["skip_reason"], "same_model_as_writer")
        self.assertTrue(replay["replayed"])
        self.assertFalse(any(item["replayed"] for item in replay["targets"][:2]))
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            {item["channel_type"] for item in first["channel_coverage"]},
            {"provider_api", "consumer_web", "mobile_app"},
        )
        self.assertFalse(first["consumer_parity"]["claim_allowed"])
        recovered = get_ai_visibility_panel_request(
            self.database_path, "panel-request"
        )
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["result"]["distinct_model_count"], 2)
        self.assertTrue(recovered["manifest"]["frozen"])
        self.assertEqual(
            [item["subrequest_id"] for item in recovered["manifest"]["targets"][:2]],
            ["panel-request:writer", "panel-request:reviewer"],
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            request_count = conn.execute(
                "SELECT COUNT(*) FROM ai_visibility_sample_requests"
            ).fetchone()[0]
            usage_count = conn.execute(
                "SELECT COUNT(*) FROM ai_usage_ledger WHERE operation = 'visibility_sample'"
            ).fetchone()[0]
        self.assertEqual(request_count, 3)
        self.assertEqual(usage_count, 2)

    def test_one_model_failure_preserves_other_completed_result(self):
        def runner(_database_path, **kwargs):
            if kwargs["settings"].model == "review-model":
                raise RuntimeError("synthetic reviewer provider failure")
            return {
                "request_id": kwargs["request_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "replayed": False,
                "payload": {
                    "data": {
                        "status": "completed",
                        "completed_sample_count": 1,
                        "requested_sample_count": 1,
                        "executions": [
                            {"provider": "qwen", "model": "writer-model"}
                        ],
                        "grounding": {"web_search_verified": False},
                    }
                },
            }

        result = self.run_panel(runner)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sampled_role_count"], 1)
        writer, reviewer, evaluator = result["targets"]
        self.assertEqual(writer["status"], "completed")
        self.assertEqual(reviewer["execution_status"], "failed")
        self.assertEqual(reviewer["failure"]["code"], "panel_target_failed")
        self.assertEqual(evaluator["execution_status"], "skipped")
        self.assertFalse(result["multi_model_executed"])

    def test_one_independent_model_is_rejected_before_any_runner_call(self):
        calls = []
        targets = [
            {
                "role": "writer",
                "role_status": {"configured": True, "fallback": False},
                "settings": self.writer,
            },
            {
                "role": "reviewer",
                "role_status": {"configured": False, "fallback": True},
                "settings": self.writer,
            },
        ]

        def runner(_database_path, **kwargs):
            calls.append(kwargs["settings"].model)
            return {
                "request_id": kwargs["request_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "replayed": False,
                "payload": {
                    "data": {
                        "status": "completed",
                        "completed_sample_count": 1,
                        "requested_sample_count": 1,
                        "executions": [
                            {"provider": "qwen", "model": "writer-model"}
                        ],
                    }
                },
            }

        with self.assertRaisesRegex(
            VisibilityPanelValidationError,
            "至少需要 2 个规范化后不同",
        ):
            self.run_panel(runner, targets=targets)

        self.assertEqual(calls, [])
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM ai_visibility_sample_requests"
                ).fetchone()[0],
                0,
            )

    def test_equivalent_endpoints_are_deduplicated_before_external_calls(self):
        calls = []
        first = AISettings(
            provider="custom",
            base_url="https://API.EXAMPLE.TEST:443/v1/",
            api_key="first-secret",
            model="Same-Model",
        )
        second = AISettings(
            provider="custom",
            base_url="https://api.example.test/v1/chat/completions",
            api_key="second-secret",
            model="same-model",
        )
        targets = [
            {
                "role": "writer",
                "role_status": {"configured": True, "fallback": False},
                "settings": first,
            },
            {
                "role": "reviewer",
                "role_status": {"configured": True, "fallback": False},
                "settings": second,
            },
        ]

        with self.assertRaises(VisibilityPanelValidationError):
            self.run_panel(
                lambda *_args, **_kwargs: calls.append("called"),
                targets=targets,
            )

        self.assertEqual(calls, [])

    def test_same_key_cannot_expand_frozen_role_manifest(self):
        calls = []

        def runner(_database_path, **kwargs):
            calls.append(kwargs["settings"].model)
            return {
                "request_id": kwargs["request_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "replayed": False,
                "payload": {
                    "data": {
                        "status": "completed",
                        "completed_sample_count": 1,
                        "requested_sample_count": 1,
                        "executions": [{
                            "provider": kwargs["settings"].provider,
                            "model": kwargs["settings"].model,
                        }],
                    }
                },
            }

        original_targets = self.targets()[:2]
        first = self.run_panel(runner, targets=original_targets)
        evaluator = AISettings(
            provider="doubao",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="evaluator-secret",
            model="evaluate-model",
        )
        expanded_targets = [
            *original_targets,
            {
                "role": "evaluator",
                "role_status": {"configured": True, "fallback": False},
                "settings": evaluator,
            },
        ]

        self.assertEqual(first["status"], "completed")
        with self.assertRaisesRegex(
            VisibilityPanelConflictError,
            "不能新增角色或修改参数",
        ):
            self.run_panel(runner, targets=expanded_targets)
        self.assertEqual(calls, ["writer-model", "review-model"])

    def test_all_completed_targets_with_one_returned_model_are_not_completed_panel(self):
        def runner(_database_path, **kwargs):
            return {
                "request_id": kwargs["request_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "replayed": False,
                "payload": {
                    "data": {
                        "status": "completed",
                        "completed_sample_count": 1,
                        "requested_sample_count": 1,
                        "executions": [{"provider": "shared", "model": "same-model"}],
                    }
                },
            }

        result = self.run_panel(runner, targets=self.targets()[:2])

        self.assertEqual(result["sampled_role_count"], 2)
        self.assertEqual(result["distinct_model_count"], 1)
        self.assertFalse(result["comparison_eligible"])
        self.assertFalse(result["multi_model_executed"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["need_action"])

    def test_started_manifest_is_readable_and_same_key_does_not_start_more_calls(self):
        started = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls = []
        outcome = {}

        def runner(_database_path, **kwargs):
            with lock:
                calls.append(kwargs["request_id"])
                if len(calls) == 2:
                    started.set()
            release.wait(timeout=5)
            return {
                "request_id": kwargs["request_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "replayed": False,
                "payload": {
                    "data": {
                        "status": "completed",
                        "completed_sample_count": 1,
                        "requested_sample_count": 1,
                        "executions": [{
                            "provider": kwargs["settings"].provider,
                            "model": kwargs["settings"].model,
                        }],
                    }
                },
            }

        def run_original():
            try:
                outcome["result"] = self.run_panel(
                    runner, targets=self.targets()[:2]
                )
            except Exception as exc:  # pragma: no cover - assertion reports it
                outcome["error"] = exc

        thread = threading.Thread(target=run_original)
        thread.start()
        self.assertTrue(started.wait(timeout=5))
        recovered = get_ai_visibility_panel_request(
            self.database_path, "panel-request"
        )
        self.assertTrue(recovered["in_progress"])
        self.assertFalse(recovered["in_progress_expired"])
        self.assertEqual(recovered["status"], "processing")
        self.assertEqual(recovered["result"]["status"], "processing")
        self.assertEqual(recovered["manifest"]["roles"], ["writer", "reviewer"])
        self.assertEqual(
            [item["execution_status"] for item in recovered["targets"]],
            ["pending", "pending"],
        )
        with self.assertRaises(VisibilityPanelInProgressError) as caught:
            self.run_panel(runner, targets=self.targets()[:2])
        self.assertEqual(caught.exception.record["request_id"], "panel-request")
        self.assertEqual(len(calls), 2)
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"]["status"], "completed")

    def test_started_parent_expires_read_only_even_when_children_completed(self):
        created_at = datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc)
        manifest = {
            "panel_version": "test-panel",
            "request_shape_sha256": "shape",
            "roles": ["writer", "reviewer"],
            "runnable_role_count": 2,
            "targets": [
                {
                    "index": 1,
                    "role": "writer",
                    "runnable": True,
                    "skip_reason": "",
                    "provider": "Qwen",
                    "model": "writer-model",
                    "identity_sha256": "writer-identity",
                    "subrequest_id": "stale-panel:writer",
                    "subrequest_idempotency_key": "stale-key:writer",
                },
                {
                    "index": 2,
                    "role": "reviewer",
                    "runnable": True,
                    "skip_reason": "",
                    "provider": "DeepSeek",
                    "model": "review-model",
                    "identity_sha256": "reviewer-identity",
                    "subrequest_id": "stale-panel:reviewer",
                    "subrequest_idempotency_key": "stale-key:reviewer",
                },
            ],
            "frozen": True,
            "automatic_role_expansion": False,
        }
        parent_storage = {
            "record_type": "ai_visibility_model_panel",
            "panel_version": "test-panel",
            "manifest": manifest,
            "aggregate_result": None,
        }
        child_results = [
            ("stale-panel:writer", "stale-key:writer", "qwen", "writer-model"),
            (
                "stale-panel:reviewer",
                "stale-key:reviewer",
                "deepseek",
                "review-model",
            ),
        ]
        timestamp = created_at.isoformat().replace("+00:00", "Z")
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility_sample_requests (
                        project_id, request_id, idempotency_key,
                        request_fingerprint, status, result_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'started', ?, ?, ?)
                    """,
                    (
                        self.project_id,
                        "stale-panel",
                        "stale-key",
                        "parent-fingerprint",
                        json.dumps(parent_storage, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
                for child_request_id, child_key, provider, model in child_results:
                    child_payload = {
                        "data": {
                            "status": "completed",
                            "completed_sample_count": 1,
                            "requested_sample_count": 1,
                            "executions": [{"provider": provider, "model": model}],
                            "grounding": {"web_search_verified": False},
                        }
                    }
                    conn.execute(
                        """
                        INSERT INTO ai_visibility_sample_requests (
                            project_id, request_id, idempotency_key,
                            request_fingerprint, status, http_status,
                            result_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'completed', 200, ?, ?, ?)
                        """,
                        (
                            self.project_id,
                            child_request_id,
                            child_key,
                            f"fingerprint:{model}",
                            json.dumps(child_payload, ensure_ascii=False),
                            timestamp,
                            timestamp,
                        ),
                    )
            before = conn.execute(
                """
                SELECT status, result_json, updated_at
                FROM ai_visibility_sample_requests
                WHERE request_id = 'stale-panel'
                """
            ).fetchone()

        fresh = get_ai_visibility_panel_request(
            self.database_path,
            "stale-panel",
            current_time=created_at
            + timedelta(seconds=PANEL_IN_PROGRESS_EXPIRY_SECONDS - 1),
        )
        self.assertEqual(fresh["status"], "processing")
        self.assertTrue(fresh["in_progress"])
        self.assertFalse(fresh["in_progress_expired"])
        self.assertEqual(fresh["result"]["status"], "processing")
        self.assertFalse(fresh["result"]["outcome_unknown"])

        expired = get_ai_visibility_panel_request(
            self.database_path,
            "stale-panel",
            current_time=created_at
            + timedelta(seconds=PANEL_IN_PROGRESS_EXPIRY_SECONDS),
        )
        self.assertEqual(expired["stored_status"], "started")
        self.assertEqual(expired["status"], "need_action")
        self.assertFalse(expired["in_progress"])
        self.assertTrue(expired["in_progress_expired"])
        self.assertEqual(
            expired["in_progress_age_seconds"],
            PANEL_IN_PROGRESS_EXPIRY_SECONDS,
        )
        self.assertEqual(expired["result"]["status"], "need_action")
        self.assertTrue(expired["result"]["outcome_unknown"])
        self.assertEqual(
            expired["result"]["failure"]["code"], "panel_outcome_unknown"
        )
        self.assertEqual(expired["result"]["distinct_model_count"], 2)
        self.assertFalse(expired["result"]["comparison_eligible"])

        with closing(sqlite3.connect(self.database_path)) as conn:
            after = conn.execute(
                """
                SELECT status, result_json, updated_at
                FROM ai_visibility_sample_requests
                WHERE request_id = 'stale-panel'
                """
            ).fetchone()
        self.assertEqual(tuple(after), tuple(before))


if __name__ == "__main__":
    unittest.main()
