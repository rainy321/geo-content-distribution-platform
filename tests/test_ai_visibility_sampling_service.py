import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.ai_service import (
    AICompletionResult,
    AIReadTimeoutError,
    AISettings,
    sample_ai_prompt_detailed,
)
from services.ai_visibility_sampling_service import (
    IN_PROGRESS_EXPIRY_SECONDS,
    VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS,
    VisibilitySamplingConflictError,
    VisibilitySamplingInProgressError,
    VisibilitySamplingValidationError,
    get_ai_visibility_sample_request,
    refresh_ai_visibility_run_locally,
    refresh_ai_visibility_sample_request_locally,
    run_ai_visibility_sample,
)


class _FakeHttpResponse:
    status_code = 200
    headers = {"X-Request-ID": "provider-request-1"}

    def json(self):
        return {
            "id": "completion-1",
            "model": "sample-model-response",
            "choices": [{"message": {"content": "只基于问题作答。"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        }


class AIVisibilitySamplingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "sampling.db"
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
                        "北京同于野科技有限公司",
                        "https://example.test",
                        "DongClaw 小龙虾",
                        "AI 软件",
                        "本地合成测试项目",
                        "[]",
                        json.dumps(["RanClaw"], ensure_ascii=False),
                    ),
                )
                self.project_id = int(cursor.lastrowid)
        self.project = {
            "id": self.project_id,
            "name": "北京同于野科技有限公司",
            "website": "https://example.test",
            "product": "DongClaw 小龙虾",
            "competitors": ["RanClaw"],
        }
        self.settings = AISettings(
            base_url="https://api.example.test/v1",
            api_key="test-secret-not-persisted",
            model="sample-model",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_sample(self, sampler, **overrides):
        values = {
            "project": self.project,
            "prompt": "哪些 AI 助手部署产品值得考虑？",
            "sample_count": 2,
            "aliases": ["DongClaw"],
            "competitors": None,
            "persist": True,
            "confirm_external_call": True,
            "settings": self.settings,
            "request_id": "sampling-request-1",
            "idempotency_key": "sampling-key-1",
            "sampler": sampler,
        }
        values.update(overrides)
        return run_ai_visibility_sample(self.database_path, **values)

    def test_single_request_reader_rejects_multi_model_panel_record(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility_sample_requests (
                        project_id, request_id, idempotency_key,
                        request_fingerprint, status, result_json
                    ) VALUES (?, ?, ?, ?, 'started', ?)
                    """,
                    (
                        self.project_id,
                        "panel-request",
                        "panel-key",
                        "f" * 64,
                        json.dumps(
                            {
                                "record_type": "ai_visibility_model_panel",
                                "manifest": {"roles": ["writer", "reviewer"]},
                                "aggregate_result": None,
                            }
                        ),
                    ),
                )

        with self.assertRaisesRegex(
            VisibilitySamplingConflictError, "多模型采样"
        ):
            get_ai_visibility_sample_request(
                self.database_path,
                "panel-request",
            )

    def test_started_request_age_and_expiry_boundary_are_read_only(self):
        created = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
        created_text = created.isoformat(timespec="seconds").replace("+00:00", "Z")
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility_sample_requests (
                        project_id, request_id, idempotency_key,
                        request_fingerprint, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'started', ?, ?)
                    """,
                    (
                        self.project_id,
                        "age-request",
                        "age-key",
                        "a" * 64,
                        created_text,
                        created_text,
                    ),
                )

        before_boundary = get_ai_visibility_sample_request(
            self.database_path,
            "age-request",
            current_time=created
            + timedelta(seconds=IN_PROGRESS_EXPIRY_SECONDS - 1),
        )
        at_boundary = get_ai_visibility_sample_request(
            self.database_path,
            "age-request",
            current_time=created + timedelta(seconds=IN_PROGRESS_EXPIRY_SECONDS),
        )

        self.assertTrue(before_boundary["in_progress"])
        self.assertEqual(
            before_boundary["in_progress_age_seconds"],
            IN_PROGRESS_EXPIRY_SECONDS - 1,
        )
        self.assertFalse(before_boundary["in_progress_expired"])
        self.assertEqual(at_boundary["in_progress_age_seconds"], 900)
        self.assertTrue(at_boundary["in_progress_expired"])
        self.assertEqual(
            at_boundary["in_progress_expiry_seconds"],
            IN_PROGRESS_EXPIRY_SECONDS,
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            stored = conn.execute(
                """
                SELECT status, result_json, created_at, updated_at
                FROM ai_visibility_sample_requests WHERE request_id = ?
                """,
                ("age-request",),
            ).fetchone()
        self.assertEqual(stored, ("started", None, created_text, created_text))

    def test_completed_request_does_not_report_in_progress_age(self):
        def sampler(**_kwargs):
            return AICompletionResult(
                content="DongClaw 小龙虾值得考虑。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        sampled = self.run_sample(sampler, sample_count=1, persist=False)
        record = get_ai_visibility_sample_request(
            self.database_path,
            sampled["request_id"],
        )

        self.assertFalse(record["in_progress"])
        self.assertIsNone(record["in_progress_age_seconds"])
        self.assertFalse(record["in_progress_expired"])

    def test_completed_samples_are_analyzed_costed_and_persisted(self):
        calls = []

        def sampler(**kwargs):
            calls.append(kwargs)
            index = len(calls)
            return AICompletionResult(
                content=(
                    "1. DongClaw 小龙虾值得考虑，2. RanClaw 可作比较。 "
                    "https://example.test/guide"
                ),
                model="sample-model-response",
                provider_request_id=f"provider-{index}",
                elapsed_ms=25 + index,
                usage={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                }
                if index == 1
                else None,
            )

        result = self.run_sample(sampler)

        self.assertEqual(result["http_status"], 200)
        self.assertFalse(result["replayed"])
        data = result["payload"]["data"]
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["completed_sample_count"], 2)
        self.assertTrue(data["persisted"])
        self.assertIsInstance(data["run_id"], int)
        self.assertTrue(data["analysis"]["methodology"]["external_model_used"])
        self.assertEqual(data["analysis"]["summary"]["sample_count"], 2)
        self.assertEqual(data["data_disclosure"]["sent_to_provider"], ["prompt"])
        self.assertIn("project_record", data["data_disclosure"]["not_sent_to_provider"])
        self.assertFalse(data["grounding"]["web_search_requested"])
        self.assertFalse(data["grounding"]["web_search_verified"])
        self.assertEqual(
            data["request_policy"]["max_output_tokens"],
            VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS,
        )
        self.assertEqual(
            data["request_policy"]["execution"],
            "sequential_stop_on_first_failure",
        )
        self.assertFalse(data["request_policy"]["connection_test_equivalent"])
        self.assertEqual([set(item) for item in calls], [{"prompt", "settings"}] * 2)
        self.assertTrue(
            all(
                item["prompt"] == "哪些 AI 助手部署产品值得考虑？"
                for item in calls
            )
        )
        self.assertTrue(
            all(
                item["settings"].max_tokens
                == VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS
                for item in calls
            )
        )

        with closing(sqlite3.connect(self.database_path)) as conn:
            operations = conn.execute(
                """
                SELECT operation, usage_status, total_tokens, estimated_cost
                FROM ai_usage_ledger ORDER BY id
                """
            ).fetchall()
            request_row = conn.execute(
                """
                SELECT status, result_json FROM ai_visibility_sample_requests
                WHERE idempotency_key = ?
                """,
                ("sampling-key-1",),
            ).fetchone()
            history_count = conn.execute(
                "SELECT COUNT(*) FROM ai_visibility_runs"
            ).fetchone()[0]
        self.assertEqual(operations[0], ("visibility_sample", "reported", 30, None))
        self.assertEqual(operations[1], ("visibility_sample", "unknown", None, None))
        self.assertEqual(request_row[0], "completed")
        self.assertIn('"status":"completed"', request_row[1])
        self.assertEqual(history_count, 1)

    def test_same_idempotency_key_replays_without_model_or_cost_call(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            return AICompletionResult(
                content="DongClaw 小龙虾",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage={"total_tokens": 4},
            )

        first = self.run_sample(sampler, sample_count=1, persist=False)
        replay = self.run_sample(
            sampler,
            sample_count=1,
            persist=False,
            request_id="sampling-request-retry",
        )

        self.assertEqual(len(calls), 1)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["request_id"], "sampling-request-1")
        self.assertEqual(replay["payload"], first["payload"])
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ai_usage_ledger").fetchone()[0],
                1,
            )

    def test_project_keywords_change_reanalyzes_same_receipt_without_provider_call(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            return AICompletionResult(
                content="同于野AI API中转站面向开发者。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage={"total_tokens": 4},
            )

        first = self.run_sample(
            sampler,
            sample_count=1,
            persist=False,
            project={**self.project, "keywords": []},
        )
        self.assertFalse(
            first["payload"]["data"]["analysis"]["samples"][0]["brand_mentioned"]
        )

        replay = self.run_sample(
            sampler,
            sample_count=1,
            persist=False,
            request_id="sampling-request-keyword-refresh",
            project={**self.project, "keywords": ["同于野"]},
        )

        self.assertEqual(calls, [1])
        self.assertTrue(replay["replayed"])
        replay_data = replay["payload"]["data"]
        self.assertTrue(replay_data["analysis"]["samples"][0]["brand_mentioned"])
        self.assertEqual(replay_data["analysis_refresh"]["status"], "refreshed")
        self.assertEqual(
            replay_data["analysis_refresh"]["reason"],
            "local_analysis_input_changed",
        )
        self.assertEqual(replay_data["analysis_refresh"]["provider_calls"], 0)
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ai_usage_ledger").fetchone()[0],
                1,
            )

    def test_historical_persisted_sample_is_refreshed_from_captured_text_only(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            return AICompletionResult(
                content="同于野AI API中转站面向开发者。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        sampled = self.run_sample(
            sampler,
            sample_count=1,
            persist=True,
            project={**self.project, "keywords": []},
        )
        request_id = sampled["request_id"]
        with closing(sqlite3.connect(self.database_path)) as conn:
            row = conn.execute(
                "SELECT id, result_json FROM ai_visibility_sample_requests"
            ).fetchone()
            historical = json.loads(row[1])
            historical_data = historical["data"]
            historical_data.pop("local_analysis", None)
            historical_data.pop("local_analysis_input_snapshot", None)
            historical_data.pop("analysis_refresh", None)
            historical_data["analysis"]["algorithm_versions"]["mention"] = (
                "mention-python-port-1.1.0"
            )
            with conn:
                conn.execute(
                    "UPDATE ai_visibility_sample_requests SET result_json = ? WHERE id = ?",
                    (json.dumps(historical, ensure_ascii=False), row[0]),
                )
            before_raw = conn.execute(
                "SELECT result_json FROM ai_visibility_sample_requests WHERE id = ?",
                (row[0],),
            ).fetchone()[0]
            before_ledger_count = conn.execute(
                "SELECT COUNT(*) FROM ai_usage_ledger"
            ).fetchone()[0]

        refreshed = refresh_ai_visibility_sample_request_locally(
            self.database_path,
            request_id,
            project={**self.project, "keywords": ["同于野"]},
        )

        self.assertEqual(calls, [1])
        data = refreshed["result"]["data"]
        self.assertTrue(data["analysis"]["samples"][0]["brand_mentioned"])
        self.assertEqual(data["analysis_refresh"]["status"], "refreshed")
        self.assertEqual(data["analysis_refresh"]["provider_calls"], 0)
        self.assertEqual(data["analysis_refresh"]["network_requests"], 0)
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT result_json FROM ai_visibility_sample_requests WHERE id = ?",
                    (row[0],),
                ).fetchone()[0],
                before_raw,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ai_usage_ledger").fetchone()[0],
                before_ledger_count,
            )

    def test_unpersisted_historical_sample_without_exact_inputs_stays_stale(self):
        def sampler(**_kwargs):
            return AICompletionResult(
                content="同于野AI API中转站面向开发者。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        sampled = self.run_sample(
            sampler,
            sample_count=1,
            persist=False,
            project={**self.project, "keywords": []},
        )
        with closing(sqlite3.connect(self.database_path)) as conn:
            row = conn.execute(
                "SELECT id, result_json FROM ai_visibility_sample_requests"
            ).fetchone()
            historical = json.loads(row[1])
            data = historical["data"]
            data.pop("local_analysis", None)
            data.pop("local_analysis_input_snapshot", None)
            data.pop("analysis_refresh", None)
            data["analysis"]["algorithm_versions"]["mention"] = (
                "mention-python-port-1.1.0"
            )
            with conn:
                conn.execute(
                    "UPDATE ai_visibility_sample_requests SET result_json = ? WHERE id = ?",
                    (json.dumps(historical, ensure_ascii=False), row[0]),
                )

        unresolved = refresh_ai_visibility_sample_request_locally(
            self.database_path,
            sampled["request_id"],
            project={**self.project, "keywords": ["同于野"]},
        )

        data = unresolved["result"]["data"]
        self.assertFalse(data["analysis"]["samples"][0]["brand_mentioned"])
        self.assertEqual(data["analysis_refresh"]["status"], "stale_unresolved")
        self.assertEqual(
            data["analysis_refresh"]["reason"], "saved_analysis_inputs_incomplete"
        )
        self.assertFalse(data["analysis_refresh"]["performed"])

    def test_persisted_run_refresh_returns_new_view_without_rewriting_history(self):
        def sampler(**_kwargs):
            return AICompletionResult(
                content="同于野AI API中转站面向开发者。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        sampled = self.run_sample(
            sampler,
            sample_count=1,
            persist=True,
            project={**self.project, "keywords": []},
        )
        run_id = sampled["payload"]["data"]["run_id"]
        with closing(sqlite3.connect(self.database_path)) as conn:
            before = conn.execute(
                "SELECT result_json FROM ai_visibility_runs WHERE id = ?", (run_id,)
            ).fetchone()[0]

        refreshed = refresh_ai_visibility_run_locally(
            self.database_path,
            run_id,
            project={**self.project, "keywords": ["同于野"]},
        )

        self.assertTrue(refreshed["result"]["samples"][0]["brand_mentioned"])
        self.assertEqual(refreshed["analysis_refresh"]["status"], "refreshed")
        self.assertEqual(refreshed["analysis_refresh"]["provider_calls"], 0)
        with closing(sqlite3.connect(self.database_path)) as conn:
            after = conn.execute(
                "SELECT result_json FROM ai_visibility_runs WHERE id = ?", (run_id,)
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_persisted_run_with_invalid_saved_inputs_keeps_old_result_and_marks_stale(self):
        def sampler(**_kwargs):
            return AICompletionResult(
                content="DongClaw 小龙虾值得考虑。",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        sampled = self.run_sample(sampler, sample_count=1, persist=True)
        run_id = sampled["payload"]["data"]["run_id"]
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE ai_visibility_runs SET coverage_items_json = ? WHERE id = ?",
                    (
                        json.dumps(
                            {"aliases": "not-an-array", "competitors": None}
                        ),
                        run_id,
                    ),
                )

        unresolved = refresh_ai_visibility_run_locally(
            self.database_path,
            run_id,
            project=self.project,
        )

        self.assertTrue(unresolved["result"]["samples"][0]["brand_mentioned"])
        self.assertEqual(
            unresolved["analysis_refresh"]["status"], "stale_unresolved"
        )
        self.assertEqual(
            unresolved["analysis_refresh"]["reason"],
            "saved_analysis_data_invalid",
        )
        self.assertFalse(unresolved["analysis_refresh"]["performed"])

    def test_changed_request_conflicts_before_new_model_call(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            return AICompletionResult(
                content="DongClaw 小龙虾",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage=None,
            )

        self.run_sample(sampler, sample_count=1, persist=False)
        with self.assertRaises(VisibilitySamplingConflictError):
            self.run_sample(
                sampler,
                prompt="另一个问题",
                sample_count=1,
                persist=False,
                request_id="sampling-request-2",
            )
        self.assertEqual(len(calls), 1)

    def test_timeout_after_one_success_stops_remaining_samples(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise AIReadTimeoutError("请求结果未知，请勿自动重试")
            return AICompletionResult(
                content="DongClaw 小龙虾",
                model="sample-model",
                provider_request_id="provider-one",
                elapsed_ms=1,
                usage={"total_tokens": 3},
            )

        result = self.run_sample(sampler, sample_count=5, persist=False)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["http_status"], 207)
        data = result["payload"]["data"]
        self.assertEqual(data["status"], "partial")
        self.assertTrue(data["need_action"])
        self.assertEqual(data["completed_sample_count"], 1)
        self.assertEqual(data["unattempted_sample_count"], 3)
        self.assertEqual(data["failure"]["code"], "provider_outcome_unknown")
        self.assertTrue(data["failure"]["outcome_unknown"])

    def test_first_timeout_is_saved_and_replayed_without_retry(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)
            raise AIReadTimeoutError("请求结果未知，请勿自动重试")

        first = self.run_sample(sampler, sample_count=3, persist=False)
        replay = self.run_sample(
            sampler,
            sample_count=3,
            persist=False,
            request_id="sampling-request-after-timeout",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["http_status"], 504)
        self.assertEqual(first["payload"]["data"]["status"], "need_action")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["payload"], first["payload"])

    def test_started_request_never_resends_automatically(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO ai_visibility_sample_requests (
                        project_id, request_id, idempotency_key,
                        request_fingerprint, status
                    ) VALUES (?, ?, ?, ?, 'started')
                    """,
                    (self.project_id, "stale-request", "stale-key", "unrelated"),
                )

        # A mismatched fingerprint is a conflict. Build a genuine started row
        # by interrupting the first sampler with a programming error instead.
        with self.assertRaises(RuntimeError):
            self.run_sample(
                lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
                sample_count=1,
                persist=False,
                request_id="crash-request",
                idempotency_key="crash-key",
            )
        with self.assertRaises(VisibilitySamplingInProgressError):
            self.run_sample(
                lambda **_kwargs: self.fail("sampler must not run"),
                sample_count=1,
                persist=False,
                request_id="retry-after-crash",
                idempotency_key="crash-key",
            )

    def test_invalid_confirmation_and_aliases_fail_before_model_call(self):
        calls = []

        def sampler(**_kwargs):
            calls.append(1)

        with self.assertRaises(VisibilitySamplingValidationError):
            self.run_sample(sampler, confirm_external_call=False)
        with self.assertRaises(VisibilitySamplingValidationError):
            self.run_sample(sampler, aliases="DongClaw")
        self.assertEqual(calls, [])
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM ai_visibility_sample_requests"
                ).fetchone()[0],
                0,
            )


class AIVisibilitySamplingTransportTests(unittest.TestCase):
    def test_transport_sends_exact_prompt_as_the_only_message_text(self):
        captured = {}
        settings = AISettings(
            base_url="https://api.example.test/v1",
            api_key="secret",
            model="sample-model",
        )

        def http_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _FakeHttpResponse()

        result = sample_ai_prompt_detailed(
            prompt="  只发送这个问题？  ", settings=settings, http_post=http_post
        )

        self.assertEqual(result.content, "只基于问题作答。")
        self.assertEqual(
            captured["json"]["messages"],
            [{"role": "user", "content": "只发送这个问题？"}],
        )
        serialized = json.dumps(captured["json"], ensure_ascii=False)
        self.assertNotIn("system", serialized)
        self.assertNotIn("project", serialized)
        self.assertNotIn("competitor", serialized)
        self.assertNotIn("secret", serialized)
if __name__ == "__main__":
    unittest.main()
