import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from services.ai_cost_ledger_service import record_ai_usage


class AIVisibilitySamplingDatabaseTests(unittest.TestCase):
    def test_existing_cost_ledger_is_migrated_without_losing_receipts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "migration.db"
            initialize_database(database_path)
            with closing(sqlite3.connect(database_path)) as conn:
                with conn:
                    project_id = conn.execute(
                        "INSERT INTO projects (name) VALUES ('Synthetic')"
                    ).lastrowid
            legacy = record_ai_usage(
                database_path,
                operation="generate",
                provider="qwen",
                model="qwen-plus",
                usage={"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
                project_id=project_id,
                reference_type="generation_run",
                reference_id="legacy-1",
            )

            # Simulate the exact pre-sampling CHECK constraint while retaining
            # a valid ledger row and all surrounding application tables.
            with closing(sqlite3.connect(database_path)) as conn:
                original_sql = conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'ai_usage_ledger'"
                ).fetchone()[0]
                old_sql = re.sub(
                    r",\s*'visibility_sample'", "", original_sql, count=1
                )
                self.assertNotIn("visibility_sample", old_sql)
                conn.execute("PRAGMA writable_schema = ON")
                conn.execute(
                    "UPDATE sqlite_master SET sql = ? "
                    "WHERE type = 'table' AND name = 'ai_usage_ledger'",
                    (old_sql,),
                )
                schema_version = conn.execute("PRAGMA schema_version").fetchone()[0]
                conn.execute(f"PRAGMA schema_version = {schema_version + 1}")
                conn.execute("PRAGMA writable_schema = OFF")
                conn.commit()

            initialize_database(database_path)
            sampled = record_ai_usage(
                database_path,
                operation="visibility_sample",
                provider="qwen",
                model="qwen-plus",
                usage=None,
                project_id=project_id,
                reference_type="visibility_sample",
                reference_id="sample-1",
            )

            with closing(sqlite3.connect(database_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT id, operation, reference_id, total_tokens
                    FROM ai_usage_ledger ORDER BY id
                    """
                ).fetchall()
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                index_names = {
                    row[1] for row in conn.execute("PRAGMA index_list(ai_usage_ledger)")
                }

            self.assertEqual(
                rows,
                [
                    (legacy["id"], "generate", "legacy-1", 10),
                    (sampled["id"], "visibility_sample", "sample-1", None),
                ],
            )
            self.assertIn("ai_visibility_sample_requests", table_names)
            self.assertIn("idx_ai_usage_reference", index_names)
            self.assertIn("idx_ai_usage_project_created", index_names)


if __name__ == "__main__":
    unittest.main()
