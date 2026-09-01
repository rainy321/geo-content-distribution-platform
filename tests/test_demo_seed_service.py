import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services.dashboard_service import get_dashboard_overview
from services.demo_seed_service import DEMO_PROJECT_NAME, seed_demo_data


class DemoSeedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "demo.db"
        initialize_database(self.db_path)
        self.now = datetime(
            2026,
            8,
            31,
            22,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_seeds_labelled_demo_project_articles_jobs_and_dashboard(self):
        result = seed_demo_data(self.db_path, now=self.now)

        self.assertTrue(result["seeded"])
        self.assertEqual(result["projects"], 1)
        self.assertEqual(result["articles"], 6)
        self.assertEqual(result["publish_jobs"], 13)

        with closing(sqlite3.connect(self.db_path)) as conn:
            project = conn.execute(
                "SELECT name, industry, product, description FROM projects"
            ).fetchone()
            article_rows = conn.execute(
                "SELECT tags, geo_score FROM articles ORDER BY id"
            ).fetchall()
            job_rows = conn.execute(
                "SELECT status, demo, message FROM publish_jobs ORDER BY id"
            ).fetchall()

        self.assertEqual(project[0], DEMO_PROJECT_NAME)
        self.assertEqual(project[1:3], ("人工智能", "企业 AI Agent"))
        self.assertIn("示例数据", project[3])
        self.assertTrue(
            all("示例数据" in json.loads(row[0]) for row in article_rows)
        )
        self.assertTrue(all(row[1] > 0 for row in article_rows))
        self.assertTrue(all(row[1] == 1 for row in job_rows))
        self.assertIn("failed", {row[0] for row in job_rows})
        self.assertIn("need_action", {row[0] for row in job_rows})
        self.assertIn("scheduled", {row[0] for row in job_rows})
        self.assertTrue(all("演示" in row[2] for row in job_rows))

        overview = get_dashboard_overview(self.db_path, now=self.now)
        self.assertEqual(overview["summary"]["today_generated"], 1)
        self.assertEqual(overview["summary"]["today_published"], 7)
        self.assertEqual(overview["summary"]["today_success"], 4)
        self.assertEqual(overview["summary"]["today_failed"], 1)
        self.assertEqual(overview["summary"]["demo_jobs_today"], 7)
        self.assertTrue(all(job["demo"] for job in overview["recent_jobs"]))

    def test_is_idempotent_after_first_seed(self):
        first = seed_demo_data(self.db_path, now=self.now)
        second = seed_demo_data(self.db_path, now=self.now + timedelta(hours=1))

        self.assertTrue(first["seeded"])
        self.assertFalse(second["seeded"])
        self.assertEqual(second["reason"], "database_not_empty")
        self.assertEqual(second["projects"], 1)
        self.assertEqual(second["articles"], 6)
        self.assertEqual(second["publish_jobs"], 13)

    def test_does_not_add_samples_to_a_user_database(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute("INSERT INTO projects (name) VALUES ('用户项目')")

        result = seed_demo_data(self.db_path, now=self.now)

        self.assertFalse(result["seeded"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            names = [row[0] for row in conn.execute("SELECT name FROM projects")]
            counts = tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("projects", "articles", "publish_jobs")
            )
        self.assertEqual(names, ["用户项目"])
        self.assertEqual(counts, (1, 0, 0))

    def test_rolls_back_all_rows_when_generation_fails(self):
        with patch(
            "services.demo_seed_service.score_geo_content",
            side_effect=RuntimeError("score unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "score unavailable"):
                seed_demo_data(self.db_path, now=self.now)

        with closing(sqlite3.connect(self.db_path)) as conn:
            counts = tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("projects", "articles", "publish_jobs")
            )
        self.assertEqual(counts, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
