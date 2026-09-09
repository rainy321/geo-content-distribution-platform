import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class AIVisibilityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "visibility.db"
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
                        "Acme Cloud",
                        "https://www.acme.com",
                        "Acme Suite",
                        "Software",
                        "Synthetic API-test project.",
                        "[]",
                        json.dumps(["RivalOS"]),
                    ),
                )
                self.project_id = cursor.lastrowid
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

    def payload(self, **overrides):
        value = {
            "project_id": self.project_id,
            "prompt": "What is the best automation platform?",
            "responses": [
                "1. RivalOS is popular. 2. Acme Suite is recommended. "
                "See https://docs.acme.com/guide?utm_source=assistant",
                {
                    "text": "Acme Cloud is reliable.",
                    "provider": "qwen",
                    "model": "qwen-plus",
                },
            ],
        }
        value.update(overrides)
        return value

    def test_analyzes_project_defaults_and_does_not_mutate_database(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            before = conn.execute(
                "SELECT name, website, product, competitors, updated_at FROM projects WHERE id = ?",
                (self.project_id,),
            ).fetchone()
            before_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "projects",
                    "articles",
                    "content_generation_runs",
                    "ai_visibility_runs",
                )
            }

        response = self.client.post(
            "/api/geo/visibility/analyze",
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 200)
        data = body["data"]
        self.assertEqual(data["project"]["name"], "Acme Cloud")
        self.assertEqual(data["summary"]["sample_count"], 2)
        self.assertEqual(data["entities"][0]["aliases"], ["Acme Suite"])
        self.assertEqual(data["entities"][1]["name"], "RivalOS")
        self.assertTrue(data["citations"][0]["is_owned"])
        self.assertEqual(data["samples"][1]["provider"], "qwen")
        self.assertFalse(data["methodology"]["external_model_used"])

        with closing(sqlite3.connect(self.database_path)) as conn:
            after = conn.execute(
                "SELECT name, website, product, competitors, updated_at FROM projects WHERE id = ?",
                (self.project_id,),
            ).fetchone()
            after_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "projects",
                    "articles",
                    "content_generation_runs",
                    "ai_visibility_runs",
                )
            }
        self.assertEqual(after, before)
        self.assertEqual(after_counts, before_counts)

    def test_accepts_numeric_project_id_and_request_overrides(self):
        response = self.client.post(
            "/api/geo/visibility/analyze",
            json=self.payload(
                project_id=str(self.project_id),
                aliases=["ACME"],
                competitors=[],
                responses=["The brand ACME is recommended."],
            ),
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertTrue(data["samples"][0]["brand_mentioned"])
        self.assertEqual(len(data["entities"]), 1)

    def test_persists_and_restores_local_history_only_when_requested(self):
        analyzed = self.client.post(
            "/api/geo/visibility/analyze",
            json=self.payload(persist=True),
        )

        self.assertEqual(analyzed.status_code, 200)
        result = analyzed.get_json()["data"]
        self.assertIsInstance(result["run_id"], int)
        self.assertRegex(result["created_at"], r"Z$")

        history = self.client.get(
            "/api/geo/visibility/runs",
            query_string={"project_id": self.project_id, "limit": 10},
        )
        detail = self.client.get(
            f"/api/geo/visibility/runs/{result['run_id']}"
        )

        self.assertEqual(history.status_code, 200)
        rows = history.get_json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], result["run_id"])
        self.assertNotIn("input_samples", rows[0])
        self.assertEqual(detail.status_code, 200)
        saved = detail.get_json()["data"]
        self.assertEqual(saved["input_samples"], self.payload()["responses"])
        self.assertEqual(saved["result"]["summary"]["sample_count"], 2)
        self.assertFalse(saved["result"]["methodology"]["external_model_used"])

    def test_returns_404_for_unknown_project(self):
        response = self.client.post(
            "/api/geo/visibility/analyze",
            json=self.payload(project_id=999_999),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["code"], 404)

    def test_returns_400_for_invalid_contract_inputs(self):
        cases = [
            (None, "请求数据不能为空"),
            ({"project_id": True}, "project_id"),
            (self.payload(prompt=""), "prompt"),
            (self.payload(responses="answer"), "responses"),
            (self.payload(responses=[{"provider": "qwen"}]), "text"),
            (self.payload(responses=["answer"] * 21), "最多支持"),
            (self.payload(aliases={"bad": "shape"}), "aliases"),
            (self.payload(persist="yes"), "persist"),
        ]
        for payload, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                response = self.client.post(
                    "/api/geo/visibility/analyze",
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_message, response.get_json()["msg"])

    def test_rejects_oversized_request_before_json_analysis(self):
        response = self.client.post(
            "/api/geo/visibility/analyze",
            data=b'{"padding":"' + (b"x" * (2 * 1024 * 1024)) + b'"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], 413)

    def test_existing_operator_access_gate_protects_endpoint(self):
        app.config["ACCESS_CONTROL_ENABLED"] = True

        response = self.client.post(
            "/api/geo/visibility/analyze",
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["data"]["authentication_required"])


if __name__ == "__main__":
    unittest.main()
