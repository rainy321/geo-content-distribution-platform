import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, "今日文章", "正文", "ready"),
                )

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_returns_dashboard_contract(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertIn("today_generated", data["summary"])
        self.assertEqual(len(data["trend"]), 7)
        self.assertEqual(len(data["platforms"]), 5)
        self.assertEqual(data["recent_jobs"], [])


if __name__ == "__main__":
    unittest.main()
