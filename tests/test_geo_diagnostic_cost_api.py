import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.ai_cost_ledger_service import record_ai_usage
from services.ai_service import AIOptimizationResult


class GEODiagnosticAndCostApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            self.project_id = conn.execute(
                "INSERT INTO projects (name) VALUES ('Synthetic Acme')"
            ).lastrowid
            self.article_id = conn.execute(
                """
                INSERT INTO articles (project_id, title, content)
                VALUES (?, 'Synthetic title', 'Synthetic Acme short content')
                """,
                (self.project_id,),
            ).lastrowid
            conn.commit()
        self.original_config = {
            "DATABASE_PATH": app.config.get("DATABASE_PATH"),
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            DATABASE_PATH=self.database_path,
            ACCESS_CONTROL_ENABLED=False,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def test_diagnostic_create_history_and_detail_contract(self):
        created = self.client.post(
            "/api/geo/diagnostics",
            json={"project_id": self.project_id},
        )
        self.assertEqual(created.status_code, 200)
        result = created.get_json()["data"]
        self.assertFalse(result["methodology"]["external_model_used"])
        self.assertTrue(result["gaps"])

        history = self.client.get(
            "/api/geo/diagnostics/runs",
            query_string={"project_id": self.project_id},
        )
        detail = self.client.get(
            f"/api/geo/diagnostics/runs/{result['run_id']}"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.get_json()["data"][0]["id"], result["run_id"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["score"], result["score"])

    def test_project_cost_summary_reports_model_usage_and_publish_non_applicability(self):
        record_ai_usage(
            self.database_path,
            operation="generate",
            provider="qwen",
            model="synthetic-model",
            usage={"input_tokens": 100, "output_tokens": 50},
            project_id=self.project_id,
            pricing_catalog={},
        )

        response = self.client.get(
            f"/api/ai-usage/projects/{self.project_id}/summary"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["summary"]["known_total_tokens"], 150)
        self.assertEqual(data["summary"]["unpriced_count"], 1)
        self.assertFalse(data["publishing"]["token_usage_applicable"])

    def test_billing_endpoint_filters_receipts_without_exposing_metadata(self):
        catalog = {
            "qwen/synthetic-model": {
                "input_per_million": "1",
                "output_per_million": "2",
                "currency": "CNY",
            }
        }
        record_ai_usage(
            self.database_path,
            operation="generate",
            provider="qwen",
            model="synthetic-model",
            usage={"input_tokens": 100, "output_tokens": 50},
            project_id=self.project_id,
            pricing_catalog=catalog,
            metadata={"usage_origin": "provider_response"},
        )
        record_ai_usage(
            self.database_path,
            operation="optimize",
            provider="qwen",
            model="synthetic-model",
            usage={"input_tokens": 10, "output_tokens": 5},
            project_id=self.project_id,
            pricing_catalog={},
        )

        response = self.client.get(
            "/api/ai-usage/billing",
            query_string={
                "project_id": self.project_id,
                "days": 30,
                "operation": "generate",
                "cost_status": "calculated",
                "limit": 200,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = response.get_json()["data"]
        self.assertEqual(data["summary"]["operation_count"], 1)
        self.assertEqual(data["summary"]["known_total_tokens"], 150)
        self.assertEqual(data["record_window"]["matched"], 1)
        self.assertEqual(data["records"][0]["project_name"], "Synthetic Acme")
        self.assertNotIn("metadata", data["records"][0])
        self.assertFalse(data["publishing"]["token_usage_applicable"])

    def test_billing_endpoint_rejects_invalid_filters(self):
        responses = (
            self.client.get("/api/ai-usage/billing", query_string={"days": -1}),
            self.client.get(
                "/api/ai-usage/billing", query_string={"operation": "publish"}
            ),
            self.client.get(
                "/api/ai-usage/billing", query_string={"project_id": "invalid"}
            ),
        )
        self.assertEqual([response.status_code for response in responses], [400, 400, 400])

    def test_price_catalog_api_saves_lists_backfills_and_deletes(self):
        pending = record_ai_usage(
            self.database_path,
            operation="generate",
            provider="qwen",
            model="synthetic-model",
            usage={"input_tokens": 100, "output_tokens": 50},
            project_id=self.project_id,
            pricing_catalog={},
        )
        saved_response = self.client.put(
            "/api/ai-usage/prices",
            json={
                "provider": "qwen",
                "model": "synthetic-model",
                "input_price_per_million": "2",
                "output_price_per_million": "6",
                "currency": "CNY",
                "source_note": "Synthetic official price",
                "apply_to_unpriced": True,
            },
        )
        self.assertEqual(saved_response.status_code, 200)
        saved = saved_response.get_json()["data"]
        self.assertEqual(saved["calculated_receipt_count"], 1)

        catalog_response = self.client.get("/api/ai-usage/prices")
        self.assertEqual(catalog_response.status_code, 200)
        self.assertEqual(catalog_response.headers["Cache-Control"], "no-store")
        catalog = catalog_response.get_json()["data"]
        self.assertEqual(catalog["entries"][0]["model"], "synthetic-model")
        self.assertEqual(catalog["detected_models"][0]["unpriced_receipt_count"], 0)

        bill = self.client.get(
            "/api/ai-usage/billing", query_string={"days": 0}
        ).get_json()["data"]
        receipt = next(item for item in bill["records"] if item["id"] == pending["id"])
        self.assertEqual(receipt["cost_status"], "calculated")

        price_id = saved["entry"]["id"]
        deleted = self.client.delete(f"/api/ai-usage/prices/{price_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get("/api/ai-usage/prices").get_json()["data"]["entries"],
            [],
        )

    def test_price_catalog_api_rejects_invalid_payload(self):
        responses = (
            self.client.put("/api/ai-usage/prices", data="not-json"),
            self.client.put(
                "/api/ai-usage/prices",
                json={
                    "provider": "qwen",
                    "model": "synthetic-model",
                    "input_price_per_million": -1,
                    "output_price_per_million": 2,
                    "currency": "CNY",
                },
            ),
            self.client.delete("/api/ai-usage/prices/999999"),
        )
        self.assertEqual([response.status_code for response in responses], [400, 400, 404])

    @patch("sau_backend.optimize_geo_content")
    def test_optimization_provider_usage_is_recorded(self, mock_optimize):
        mock_optimize.return_value = AIOptimizationResult(
            article={
                "title": "Optimized title",
                "summary": "summary",
                "content": "Synthetic Acme optimized content",
                "tags": [],
                "faq": [],
            },
            provider="dashscope",
            model="qwen-plus",
            provider_request_id="optimize-provider-request",
            elapsed_ms=50,
            usage={"input_tokens": 80, "output_tokens": 40, "total_tokens": 120},
        )

        response = self.client.post(f"/api/articles/{self.article_id}/optimize")

        self.assertEqual(response.status_code, 200)
        receipt = response.get_json()["data"]["ai_usage"]
        self.assertEqual(receipt["operation"], "optimize")
        self.assertEqual(receipt["provider"], "qwen")
        self.assertEqual(receipt["total_tokens"], 120)
        summary = self.client.get(
            f"/api/ai-usage/projects/{self.project_id}/summary"
        ).get_json()["data"]
        self.assertEqual(summary["summary"]["known_total_tokens"], 120)

    @patch("sau_backend.record_ai_usage")
    @patch("sau_backend.optimize_geo_content")
    def test_optimization_returns_preview_when_cost_receipt_write_fails(
        self,
        mock_optimize,
        mock_record_usage,
    ):
        mock_optimize.return_value = AIOptimizationResult(
            article={
                "title": "Optimized title",
                "summary": "summary",
                "content": "Synthetic Acme optimized content",
                "tags": [],
                "faq": [],
            },
            provider="qwen",
            model="qwen-plus",
            provider_request_id="charged-provider-request",
            elapsed_ms=50,
            usage={"input_tokens": 80, "output_tokens": 40, "total_tokens": 120},
        )
        mock_record_usage.side_effect = sqlite3.OperationalError("database busy")

        response = self.client.post(f"/api/articles/{self.article_id}/optimize")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["optimized"]["title"], "Optimized title")
        self.assertIsNone(data["ai_usage"])
        self.assertEqual(data["warnings"][0]["code"], "cost_receipt_not_recorded")
        self.assertIn("不要直接重试", data["warnings"][0]["message"])


if __name__ == "__main__":
    unittest.main()
