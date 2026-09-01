import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.dashboard_service import get_dashboard_overview


class DashboardServiceTests(unittest.TestCase):
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
                article_today = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, content, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, "今日文章", "正文", "ready", "2026-09-02 01:00:00"),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, content, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, "昨日文章", "正文", "ready", "2026-09-01 01:00:00"),
                )
                conn.executemany(
                    """
                    INSERT INTO publish_jobs (
                        article_id, platform, status, message, demo,
                        created_at, started_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            article_today,
                            "zhihu",
                            "success",
                            "演示发布完成",
                            1,
                            "2026-09-02 02:00:00",
                            "2026-09-02 02:00:01",
                            "2026-09-02 02:00:02",
                        ),
                        (
                            article_today,
                            "toutiao",
                            "failed",
                            "网络异常",
                            0,
                            "2026-09-02 03:00:00",
                            "2026-09-02 03:00:01",
                            "2026-09-02 03:00:02",
                        ),
                        (
                            article_today,
                            "sohu",
                            "scheduled",
                            "",
                            0,
                            "2026-09-02 04:00:00",
                            None,
                            None,
                        ),
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (9, "zhihu.json", "知乎一号", 1),
                        (9, "zhihu-2.json", "知乎二号", 1),
                        (7, "toutiao.json", "头条一号", 0),
                        (1, "xhs.json", "小红书一号", 1),
                    ],
                )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builds_real_summary_trend_platforms_and_recent_jobs(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone(timedelta(hours=8)))

        overview = get_dashboard_overview(self.db_path, now=now)

        self.assertEqual(
            overview["summary"],
            {
                "today_generated": 1,
                "today_published": 3,
                "today_success": 1,
                "today_failed": 1,
                "connected_media": 2,
                "demo_jobs_today": 1,
            },
        )
        self.assertEqual(len(overview["trend"]), 7)
        self.assertEqual(overview["trend"][-1]["generated"], 1)
        self.assertEqual(overview["trend"][-1]["published"], 3)
        self.assertEqual(overview["trend"][-1]["success"], 1)
        platforms = {item["key"]: item for item in overview["platforms"]}
        self.assertTrue(platforms["zhihu"]["connected"])
        self.assertEqual(platforms["zhihu"]["account_count"], 2)
        self.assertFalse(platforms["toutiao"]["connected"])
        self.assertTrue(platforms["xiaohongshu"]["connected"])
        self.assertEqual(overview["recent_jobs"][0]["status"], "scheduled")
        self.assertTrue(any(job["demo"] for job in overview["recent_jobs"]))

    def test_hong_kong_day_includes_previous_utc_date_evening(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            project_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
            with conn:
                conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, content, status, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, "香港凌晨文章", "正文", "ready", "2026-09-01 17:00:00"),
                )
        now = datetime(2026, 9, 2, 1, 30, tzinfo=timezone(timedelta(hours=8)))

        overview = get_dashboard_overview(self.db_path, now=now)

        self.assertEqual(overview["summary"]["today_generated"], 2)

    def test_rejects_invalid_recent_limit(self):
        with self.assertRaisesRegex(ValueError, "recent_limit"):
            get_dashboard_overview(self.db_path, recent_limit=0)


if __name__ == "__main__":
    unittest.main()
