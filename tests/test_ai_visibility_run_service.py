import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services.ai_visibility_run_service import (
    VisibilityRunDataError,
    VisibilityRunNotFoundError,
    VisibilityRunValidationError,
    get_ai_visibility_run,
    list_ai_visibility_runs,
    save_ai_visibility_run,
)
from services.project_service import ProjectNotFoundError


class AIVisibilityRunServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "visibility-history.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("合成测试品牌",),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save(self, **overrides):
        values = {
            "project_id": self.project_id,
            "prompt": "  哪些自动化平台值得推荐？  ",
            "input_samples": [
                {"provider": "synthetic", "text": "合成测试品牌值得推荐。"}
            ],
            "coverage_items": {
                "aliases": ["Synthetic Brand"],
                "competitors": ["示例竞品"],
            },
            "result": {
                "summary": {"mention_rate": 1.0, "sample_count": 1},
                "warnings": ["样本少于 5 条"],
                "samples": [{"id": "sample-1", "brand_mentioned": True}],
            },
            "algorithm_version": "ai-visibility-integration-1.0.0",
        }
        values.update(overrides)
        return save_ai_visibility_run(self.database_path, **values)

    def test_database_initialization_creates_table_and_index_idempotently(self):
        initialize_database(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as conn:
            table = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("ai_visibility_runs",),
            ).fetchone()
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(ai_visibility_runs)")
            }

        self.assertIsNotNone(table)
        self.assertIn("idx_ai_visibility_runs_project_created", indexes)

    def test_save_and_get_round_trip_unicode_json_and_utc_timestamp(self):
        saved = self._save()
        loaded = get_ai_visibility_run(self.database_path, saved["id"])

        self.assertEqual(saved, loaded)
        self.assertEqual(saved["prompt"], "哪些自动化平台值得推荐？")
        self.assertEqual(saved["input_samples"][0]["provider"], "synthetic")
        self.assertEqual(saved["coverage_items"]["competitors"], ["示例竞品"])
        self.assertEqual(saved["result"]["summary"]["mention_rate"], 1.0)
        self.assertRegex(
            saved["created_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )

        with closing(sqlite3.connect(self.database_path)) as conn:
            raw = conn.execute(
                """
                SELECT input_samples_json, coverage_items_json, result_json
                FROM ai_visibility_runs WHERE id = ?
                """,
                (saved["id"],),
            ).fetchone()
        self.assertEqual(json.loads(raw[0]), saved["input_samples"])
        self.assertEqual(json.loads(raw[1]), saved["coverage_items"])
        self.assertEqual(json.loads(raw[2]), saved["result"])

    def test_list_returns_newest_lightweight_summaries_and_honors_limit(self):
        first = self._save(prompt="第一次")
        second = self._save(prompt="第二次")

        rows = list_ai_visibility_runs(
            self.database_path,
            project_id=self.project_id,
            limit=1,
        )

        self.assertEqual([row["id"] for row in rows], [second["id"]])
        self.assertEqual(rows[0]["prompt"], "第二次")
        self.assertEqual(rows[0]["summary"]["sample_count"], 1)
        self.assertEqual(rows[0]["warnings_count"], 1)
        self.assertNotIn("input_samples", rows[0])
        self.assertNotIn("coverage_items", rows[0])
        self.assertNotIn("result", rows[0])
        self.assertLess(first["id"], second["id"])

    def test_project_scope_and_foreign_key_contracts_are_enforced(self):
        with self.assertRaises(ProjectNotFoundError):
            self._save(project_id=999_999)
        with self.assertRaises(ProjectNotFoundError):
            list_ai_visibility_runs(
                self.database_path,
                project_id=999_999,
            )

        saved = self._save()
        with self.assertRaises(ProjectNotFoundError):
            get_ai_visibility_run(
                self.database_path,
                saved["id"],
                project_id=999_999,
            )
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute("DELETE FROM projects WHERE id = ?", (self.project_id,))
            count = conn.execute("SELECT COUNT(*) FROM ai_visibility_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_missing_or_wrong_project_run_returns_not_found(self):
        saved = self._save()
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                other_project = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("另一个项目",),
                ).lastrowid

        with self.assertRaises(VisibilityRunNotFoundError):
            get_ai_visibility_run(self.database_path, 999_999)
        with self.assertRaises(VisibilityRunNotFoundError):
            get_ai_visibility_run(
                self.database_path,
                saved["id"],
                project_id=other_project,
            )

    def test_rejects_invalid_identity_limit_and_payload_shapes(self):
        invalid_save_cases = [
            ({"project_id": True}, "project_id"),
            ({"prompt": "   "}, "prompt"),
            ({"input_samples": {"text": "bad"}}, "input_samples"),
            ({"coverage_items": "bad"}, "coverage_items"),
            ({"result": []}, "result"),
            ({"algorithm_version": ""}, "algorithm_version"),
            ({"result": {"score": math.nan}}, "result"),
            ({"result": {"unsupported": {1, 2}}}, "result"),
        ]
        for changes, field_name in invalid_save_cases:
            with self.subTest(field_name=field_name, changes=changes):
                with self.assertRaisesRegex(
                    VisibilityRunValidationError,
                    field_name,
                ):
                    self._save(**changes)

        for invalid_limit in (True, 0, 101, "20"):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(VisibilityRunValidationError):
                    list_ai_visibility_runs(
                        self.database_path,
                        project_id=self.project_id,
                        limit=invalid_limit,
                    )

    def test_failed_insert_rolls_back_without_partial_history(self):
        with self.assertRaises(ProjectNotFoundError):
            self._save(project_id=999_999)

        with closing(sqlite3.connect(self.database_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM ai_visibility_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_corrupt_json_or_timestamp_is_reported_instead_of_hidden(self):
        saved = self._save()
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE ai_visibility_runs SET result_json = ? WHERE id = ?",
                    ("not-json", saved["id"]),
                )
        with self.assertRaises(VisibilityRunDataError):
            get_ai_visibility_run(self.database_path, saved["id"])

        saved = self._save(prompt="时间校验")
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE ai_visibility_runs SET created_at = ? WHERE id = ?",
                    ("2026-09-09 12:00:00", saved["id"]),
                )
        with self.assertRaises(VisibilityRunDataError):
            get_ai_visibility_run(self.database_path, saved["id"])


if __name__ == "__main__":
    unittest.main()
