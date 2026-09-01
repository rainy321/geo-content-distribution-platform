import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.publish_job_service import create_publish_job, get_publish_job
from services.publish_scheduler_service import (
    queue_due_publish_jobs,
    run_publish_scheduler_tick,
)


class PublishSchedulerServiceTests(unittest.TestCase):
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
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, "AI Agent 指南", "正文", "ready"),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _scheduled_job(self, publish_at, *, demo=False, platform="zhihu"):
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            publish_at=publish_at,
            demo=demo,
        )

    def test_promotes_only_due_jobs_and_is_idempotent(self):
        now = datetime(2026, 9, 2, 10, 0)
        due = self._scheduled_job(now - timedelta(minutes=1))
        future = self._scheduled_job(now + timedelta(minutes=1), platform="toutiao")

        first = queue_due_publish_jobs(self.db_path, now=now)
        second = queue_due_publish_jobs(self.db_path, now=now)

        self.assertEqual([job["id"] for job in first], [due["id"]])
        self.assertEqual(first[0]["status"], "queued")
        self.assertIn("已到计划时间", first[0]["message"])
        self.assertEqual(second, [])
        self.assertEqual(get_publish_job(self.db_path, future["id"])["status"], "scheduled")

    def test_compares_timezone_offsets_by_absolute_time(self):
        due = self._scheduled_job("2026-09-02T18:00:00+08:00")
        future = self._scheduled_job(
            "2026-09-02T18:01:00+08:00",
            platform="toutiao",
        )
        now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)

        promoted = queue_due_publish_jobs(self.db_path, now=now)

        self.assertEqual([job["id"] for job in promoted], [due["id"]])
        self.assertEqual(get_publish_job(self.db_path, future["id"])["status"], "scheduled")

    def test_naive_reference_uses_scheduled_time_timezone(self):
        due = self._scheduled_job("2026-09-02T18:00:00+08:00")

        promoted = queue_due_publish_jobs(
            self.db_path,
            now=datetime(2026, 9, 2, 18, 0),
        )

        self.assertEqual([job["id"] for job in promoted], [due["id"]])

    def test_respects_limit_in_chronological_order(self):
        now = datetime(2026, 9, 2, 10, 0)
        later = self._scheduled_job(now - timedelta(minutes=1), platform="toutiao")
        earlier = self._scheduled_job(now - timedelta(minutes=2), platform="zhihu")

        promoted = queue_due_publish_jobs(self.db_path, now=now, limit=1)

        self.assertEqual([job["id"] for job in promoted], [earlier["id"]])
        self.assertEqual(get_publish_job(self.db_path, later["id"])["status"], "scheduled")

    def test_tick_executes_due_demo_but_only_queues_real_job(self):
        now = datetime(2026, 9, 2, 10, 0)
        demo = self._scheduled_job(now - timedelta(seconds=1), demo=True)
        real = self._scheduled_job(
            now - timedelta(seconds=1),
            demo=False,
            platform="toutiao",
        )

        result = run_publish_scheduler_tick(self.db_path, now=now)

        self.assertEqual(result["promoted_count"], 2)
        self.assertEqual(result["executed_demo_count"], 1)
        self.assertEqual(get_publish_job(self.db_path, demo["id"])["status"], "success")
        self.assertEqual(get_publish_job(self.db_path, real["id"])["status"], "queued")

    def test_rejects_invalid_limit(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            queue_due_publish_jobs(self.db_path, limit=0)


if __name__ == "__main__":
    unittest.main()
