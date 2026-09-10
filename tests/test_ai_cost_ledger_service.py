import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services.ai_cost_ledger_service import (
    AIUsageLedgerError,
    canonicalize_provider,
    get_ai_usage_bill,
    get_ai_usage_records_by_references,
    get_project_ai_cost_summary,
    record_ai_usage,
    summarize_ai_usage_records,
)
from services.content_engine_contract import ContentGenerationRequest, ContentGenerationResult
from services.content_generation_run_service import begin_or_replay, mark_succeeded


class AIUsageLedgerServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.project_id = conn.execute(
                "INSERT INTO projects (name) VALUES ('Acme')"
            ).lastrowid
            conn.commit()
        self.catalog = {
            "dashscope/qwen-plus": {
                "input_per_million": "2",
                "output_per_million": "6",
                "currency": "CNY",
            }
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_records_reported_tokens_and_immutable_price_snapshot(self):
        record = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="dashscope",
            model="qwen-plus",
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
            project_id=self.project_id,
            reference_type="generation_run",
            reference_id=7,
            pricing_catalog=self.catalog,
        )

        self.assertEqual(record["usage_status"], "reported")
        self.assertEqual(record["provider"], "qwen")
        self.assertEqual(record["canonical_provider"], "qwen")
        self.assertEqual(record["input_tokens"], 1000)
        self.assertEqual(record["output_tokens"], 500)
        self.assertEqual(record["total_tokens"], 1500)
        self.assertEqual(record["input_price_per_million"], "2")
        self.assertEqual(record["output_price_per_million"], "6")
        self.assertEqual(record["estimated_cost"], "0.005")
        self.assertEqual(record["currency"], "CNY")
        self.assertEqual(record["cost_status"], "calculated")
        self.assertEqual(
            record["pricing_source"], "environment:dashscope/qwen-plus"
        )

        replay = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="different-value-is-not-overwritten",
            model="different-model",
            usage={"total_tokens": 999999},
            project_id=self.project_id,
            reference_type="generation_run",
            reference_id=7,
            pricing_catalog={},
        )
        self.assertEqual(replay["id"], record["id"])
        self.assertEqual(replay["estimated_cost"], "0.005")

    def test_missing_usage_and_missing_price_are_not_reported_as_zero_cost(self):
        unknown = record_ai_usage(
            self.database_path,
            operation="optimize",
            provider="dashscope",
            model="qwen-plus",
            usage=None,
            usage_status="unknown",
            project_id=self.project_id,
        )
        unpriced = record_ai_usage(
            self.database_path,
            operation="platform_adapt",
            provider="colleague",
            model="adapter-v1",
            usage={"input_tokens": 10, "output_tokens": 20},
            project_id=self.project_id,
            pricing_catalog={},
        )

        self.assertIsNone(unknown["estimated_cost"])
        self.assertEqual(unknown["cost_status"], "unknown_usage")
        self.assertEqual(unpriced["cost_status"], "unpriced")
        self.assertIsNone(unpriced["estimated_cost"])

        summary = get_project_ai_cost_summary(
            self.database_path, project_id=self.project_id
        )
        self.assertEqual(summary["summary"]["operation_count"], 2)
        self.assertEqual(summary["summary"]["unknown_usage_count"], 1)
        self.assertEqual(summary["summary"]["unpriced_count"], 1)
        self.assertFalse(summary["publishing"]["token_usage_applicable"])

    def test_rejects_publishing_as_fake_model_token_operation(self):
        with self.assertRaisesRegex(AIUsageLedgerError, "平台发布本身不计 Token"):
            record_ai_usage(
                self.database_path,
                operation="publish",
                provider="browser",
                model="none",
                usage={"total_tokens": 0},
                project_id=self.project_id,
            )

    def test_successful_generation_automatically_creates_one_usage_receipt(self):
        request = ContentGenerationRequest.create(
            project_context={"id": self.project_id, "name": "Acme"},
            topic="Synthetic topic",
            keywords=["automation"],
            length=600,
            content_type="行业科普",
            request_id="cost-test-request",
            idempotency_key="cost-test-idempotency",
        )
        run = begin_or_replay(
            self.database_path,
            request,
            configured_engine="qwen",
        )
        result = ContentGenerationResult(
            title="Synthetic title",
            summary="Synthetic summary",
            content="Synthetic content",
            tags=("automation",),
            faq=(),
            sources=(),
            engine="qwen",
            engine_version="qwen-plus",
            request_id=request.request_id,
            trace_id="provider-request-1",
            elapsed_ms=100,
            usage={"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        )

        mark_succeeded(
            self.database_path,
            run.run_id,
            result,
            local_score=80,
            local_analysis={"dimensions": {}},
        )
        summary = get_project_ai_cost_summary(
            self.database_path, project_id=self.project_id
        )

        self.assertEqual(summary["summary"]["operation_count"], 1)
        self.assertEqual(summary["summary"]["known_total_tokens"], 300)
        self.assertEqual(summary["recent"][0]["reference_type"], "generation_run")
        self.assertEqual(summary["recent"][0]["reference_id"], str(run.run_id))

    def test_qwen_generation_and_optimization_share_canonical_catalog_price(self):
        catalog = {
            "qwen/qwen-plus": {
                "input_per_million": "2",
                "output_per_million": "6",
                "currency": "CNY",
            }
        }
        generated = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="qwen",
            model="qwen-plus",
            usage={"input_tokens": 100, "output_tokens": 50},
            project_id=self.project_id,
            pricing_catalog=catalog,
        )
        optimized = record_ai_usage(
            self.database_path,
            operation="optimize",
            provider="dashscope",
            model="qwen-plus",
            usage={"input_tokens": 80, "output_tokens": 40},
            project_id=self.project_id,
            pricing_catalog=catalog,
        )

        self.assertEqual(generated["provider"], "qwen")
        self.assertEqual(optimized["provider"], "qwen")
        self.assertEqual(generated["cost_status"], "calculated")
        self.assertEqual(optimized["cost_status"], "calculated")
        # Simulate an immutable receipt created by ledger 1.0.0. The raw row
        # remains legacy ``dashscope``, while current summaries canonicalize it.
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute(
                "UPDATE ai_usage_ledger SET provider = 'dashscope' WHERE id = ?",
                (generated["id"],),
            )
            conn.commit()
        summary = get_project_ai_cost_summary(
            self.database_path, project_id=self.project_id
        )
        self.assertEqual(summary["summary"]["operation_count"], 2)
        self.assertEqual(summary["summary"]["unpriced_count"], 0)
        self.assertEqual(len(summary["by_model"]), 1)
        self.assertEqual(summary["by_model"][0]["provider"], "qwen")
        self.assertEqual(summary["by_model"][0]["operation_count"], 2)
        legacy = next(item for item in summary["recent"] if item["id"] == generated["id"])
        self.assertEqual(legacy["provider"], "dashscope")
        self.assertEqual(legacy["canonical_provider"], "qwen")

    def test_non_qwen_openai_compatible_provider_is_not_collapsed(self):
        self.assertEqual(
            canonicalize_provider("custom.example.com", "qwen-compatible-v1"),
            "custom.example.com",
        )
        self.assertEqual(canonicalize_provider("dashscope", "wanx-v1"), "dashscope")

    def test_reference_lookup_and_batch_summary_keep_unknowns_explicit(self):
        reported = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="dashscope",
            model="qwen-plus",
            usage={"input_tokens": 100, "output_tokens": 50},
            project_id=self.project_id,
            reference_type="generation_run",
            reference_id=7,
            pricing_catalog=self.catalog,
        )
        unknown = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="dashscope",
            model="qwen-plus",
            usage=None,
            usage_status="unknown",
            project_id=self.project_id,
            reference_type="generation_run",
            reference_id=8,
            pricing_catalog=self.catalog,
        )

        records = get_ai_usage_records_by_references(
            self.database_path,
            operation="generate",
            reference_type="generation_run",
            reference_ids=[8, 999, 7, 7],
        )
        self.assertEqual(set(records), {"7", "8"})
        self.assertEqual(records["7"]["id"], reported["id"])
        self.assertEqual(records["8"]["id"], unknown["id"])

        summary = summarize_ai_usage_records(
            list(records.values()),
            expected_item_count=3,
        )
        self.assertEqual(summary["operation_count"], 2)
        self.assertEqual(summary["known_total_tokens"], 150)
        self.assertEqual(summary["known_token_record_count"], 1)
        self.assertEqual(summary["unknown_usage_count"], 2)
        self.assertEqual(summary["unknown_cost_item_count"], 2)
        self.assertEqual(summary["missing_receipt_item_count"], 1)
        self.assertEqual(
            summary["calculated_costs"],
            [{"currency": "CNY", "estimated_cost": "0.0005"}],
        )

    def test_batch_summary_uses_null_when_no_provider_reported_tokens(self):
        summary = summarize_ai_usage_records([], expected_item_count=2)

        self.assertIsNone(summary["known_total_tokens"])
        self.assertIsNone(summary["token_totals"]["input_tokens"])
        self.assertIsNone(summary["token_totals"]["output_tokens"])
        self.assertEqual(summary["unknown_token_item_count"], 2)
        self.assertEqual(summary["unknown_cost_item_count"], 2)
        self.assertEqual(summary["calculated_costs"], [])

    def test_cross_project_bill_keeps_full_totals_and_bounded_receipt_window(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            second_project_id = conn.execute(
                "INSERT INTO projects (name) VALUES ('Second project')"
            ).lastrowid
            conn.commit()
        record_ai_usage(
            self.database_path,
            operation="generate",
            provider="qwen",
            model="qwen-plus",
            usage={"input_tokens": 1000, "output_tokens": 500},
            project_id=self.project_id,
            pricing_catalog=self.catalog,
            metadata={"usage_origin": "provider_response"},
        )
        record_ai_usage(
            self.database_path,
            operation="optimize",
            provider="qwen",
            model="qwen-plus",
            usage={"input_tokens": 200, "output_tokens": 100},
            project_id=self.project_id,
            pricing_catalog={},
        )
        record_ai_usage(
            self.database_path,
            operation="visibility_sample",
            provider="custom.example.com",
            model="sample-v1",
            usage=None,
            usage_status="unknown",
            project_id=second_project_id,
            pricing_catalog={},
        )

        bill = get_ai_usage_bill(self.database_path, days=0, limit=1)

        self.assertEqual(bill["summary"]["operation_count"], 3)
        self.assertEqual(bill["summary"]["known_total_tokens"], 1800)
        self.assertEqual(bill["summary"]["calculated_cost_count"], 1)
        self.assertEqual(bill["summary"]["unknown_cost_count"], 2)
        self.assertEqual(
            bill["summary"]["calculated_costs"],
            [{"currency": "CNY", "estimated_cost": "0.005"}],
        )
        self.assertEqual(bill["record_window"]["matched"], 3)
        self.assertEqual(bill["record_window"]["returned"], 1)
        self.assertTrue(bill["record_window"]["has_more"])
        self.assertEqual(len(bill["records"]), 1)
        self.assertNotIn("metadata", bill["records"][0])
        self.assertEqual(len(bill["by_project"]), 2)
        self.assertEqual(len(bill["by_operation"]), 3)
        self.assertFalse(bill["publishing"]["token_usage_applicable"])

        project_bill = get_ai_usage_bill(
            self.database_path,
            project_id=self.project_id,
            days=30,
            cost_status="calculated",
        )
        self.assertEqual(project_bill["summary"]["operation_count"], 1)
        self.assertEqual(project_bill["records"][0]["project_name"], "Acme")
        self.assertEqual(project_bill["filters"]["cost_status"], "calculated")

    def test_cross_project_bill_rejects_invalid_filters(self):
        invalid_calls = (
            {"days": -1},
            {"days": 3651},
            {"limit": 501},
            {"operation": "publish"},
            {"cost_status": "free"},
            {"project_id": 999999},
        )
        for values in invalid_calls:
            with self.subTest(values=values), self.assertRaises(AIUsageLedgerError):
                get_ai_usage_bill(self.database_path, **values)


if __name__ == "__main__":
    unittest.main()
