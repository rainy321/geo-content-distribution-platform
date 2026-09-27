import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from db.createTable import initialize_database
from services.publish_policy_service import (
    PublishPolicyViolation,
    assert_can_reserve_publish,
    evaluate_publish_reservation,
)


class PublishPolicyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "policy.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('风控测试品牌')"
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '发布风控测试', '正文', 'ready')
                    """,
                    (project_id,),
                ).lastrowid
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (7, 'toutiao.json', '头条测试号', 1)
                    """
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_job(self, *, platform="toutiao", created_at, status="success"):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO publish_jobs (
                        article_id, account_id, platform, status, demo, created_at
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (
                        self.article_id,
                        self.account_id,
                        platform,
                        status,
                        created_at,
                    ),
                )

    def test_allows_first_reserved_slot_and_marks_manual_activity_unknown(self):
        with closing(self._connect()) as conn:
            decision = evaluate_publish_reservation(
                conn,
                account_id=self.account_id,
                platform="toutiao",
                now=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc),
            )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["effective_limit"], 2)
        self.assertEqual(decision["remaining"], 2)
        self.assertTrue(decision["manual_activity_unknown"])

    def test_blocks_minimum_interval_before_rolling_limit(self):
        self._insert_job(created_at="2026-09-08 09:00:00")

        with closing(self._connect()) as conn:
            decision = evaluate_publish_reservation(
                conn,
                account_id=self.account_id,
                platform="toutiao",
                now=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason_code"], "minimum_interval")
        self.assertEqual(decision["minimum_interval_hours"], 3)
        self.assertIsNotNone(decision["next_allowed_at"])

    def test_blocks_conservative_new_account_rolling_limit(self):
        self._insert_job(created_at="2026-09-08 09:00:00")
        self._insert_job(created_at="2026-09-08 13:00:00")

        with closing(self._connect()) as conn:
            decision = evaluate_publish_reservation(
                conn,
                account_id=self.account_id,
                platform="toutiao",
                now=datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc),
            )
            with self.assertRaises(PublishPolicyViolation):
                assert_can_reserve_publish(
                    conn,
                    account_id=self.account_id,
                    platform="toutiao",
                    publish_at="2026-09-08T17:00:00+00:00",
                )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason_code"], "rolling_limit_reached")
        self.assertEqual(decision["used_24h"], 2)
        self.assertEqual(decision["stable_limit"], 5)

    def test_paused_platform_is_not_overridable(self):
        with closing(self._connect()) as conn:
            decision = evaluate_publish_reservation(
                conn,
                account_id=self.account_id,
                platform="douyin",
                now=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason_code"], "platform_delivery_paused")
        self.assertEqual(decision["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
