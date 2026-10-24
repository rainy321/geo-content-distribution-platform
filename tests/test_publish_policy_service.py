import sqlite3
import tempfile
import unittest
import random
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services.publish_policy_service import (
    PublishPolicyViolation,
    assert_can_reserve_publish,
    evaluate_publish_reservation,
    _max_existing_in_rolling_window,
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

    def _insert_job(self, *, platform="toutiao", created_at, status="success",
                    publish_at=None, started_at=None, demo=0, account_id=None):
        with closing(self._connect()) as conn:
            with conn:
                if status == 'unknown':
                    # Simulate a legacy database that predates the status CHECK.
                    conn.execute('PRAGMA ignore_check_constraints = ON')
                cursor = conn.execute(
                    """
                    INSERT INTO publish_jobs (
                        article_id, account_id, platform, status, demo, created_at,
                        publish_at, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.article_id,
                        self.account_id if account_id is None else account_id,
                        platform,
                        status,
                        demo,
                        created_at,
                        publish_at,
                        started_at,
                    ),
                )
                return cursor.lastrowid

    def _decision(self, reference, **kwargs):
        with closing(self._connect()) as conn:
            return evaluate_publish_reservation(conn, account_id=self.account_id,
                                                platform="toutiao", publish_at=reference, **kwargs)

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

    def test_old_schedule_executed_today_counts_actual_attempt_once(self):
        self._insert_job(created_at='2026-09-01 00:00:00', publish_at='2026-09-02T00:00:00Z',
                         started_at='2026-10-25 08:00:00', status='need_action')
        decision = self._decision('2026-10-25T12:00:00Z')
        self.assertEqual(decision['used_24h'], 1)
        self.assertEqual(decision['attempted_24h'], 1)
        self.assertEqual(decision['last_attempt_at'], '2026-10-25T08:00:00+00:00')
        self.assertEqual(decision['remaining'], 1)

    def test_today_created_future_schedule_only_reserves_its_reference_window(self):
        self._insert_job(created_at='2026-10-25 09:00:00',
                         publish_at='2026-10-30T09:00:00Z', status='scheduled')
        now = self._decision('2026-10-25T12:00:00Z')
        future = self._decision('2026-10-30T13:00:00Z')
        self.assertEqual((now['used_24h'], now['remaining']), (0, 2))
        self.assertEqual((future['used_24h'], future['remaining']), (1, 1))
        self.assertEqual(future['attempted_24h'], 0)

    def test_exact_rolling_24h_boundary_expires_without_early_release(self):
        self._insert_job(created_at='2026-10-24 12:00:00')
        self._insert_job(created_at='2026-10-24 16:00:00')
        before = self._decision('2026-10-25T11:59:59.999999Z')
        boundary = self._decision('2026-10-25T12:00:00Z')
        self.assertFalse(before['allowed'])
        self.assertEqual(before['reason_code'], 'rolling_limit_reached')
        self.assertEqual(boundary['used_24h'], 1)
        self.assertTrue(boundary['allowed'])

    def test_minimum_interval_boundary_is_inclusive_but_not_early(self):
        self._insert_job(created_at='2026-10-25 09:00:00')
        self.assertEqual(self._decision('2026-10-25T11:59:59.999999Z')['reason_code'], 'minimum_interval')
        self.assertTrue(self._decision('2026-10-25T12:00:00Z')['allowed'])

    def test_schedule_checks_reservations_after_and_before_candidate(self):
        self._insert_job(created_at='2026-10-01 00:00:00', status='scheduled',
                         publish_at='2026-10-30T08:00:00Z')
        self._insert_job(created_at='2026-10-01 00:00:00', status='scheduled',
                         publish_at='2026-10-30T16:00:00Z')
        decision = self._decision('2026-10-30T12:00:00Z')
        self.assertFalse(decision['allowed'])
        self.assertEqual(decision['used_24h'], 2)
        self.assertEqual(decision['reason_code'], 'rolling_limit_reached')

    def test_failed_pending_queued_and_unknown_states_do_not_release_reservations(self):
        for status in ('scheduled', 'queued', 'processing', 'success', 'failed', 'need_action', 'unknown'):
            with self.subTest(status=status):
                with closing(self._connect()) as conn, conn:
                    conn.execute('DELETE FROM publish_jobs')
                self._insert_job(created_at='2026-10-25 09:00:00', status=status)
                decision = self._decision('2026-10-25T10:00:00Z')
                self.assertFalse(decision['allowed'])
                self.assertEqual(decision['used_24h'], 1)
                self.assertEqual(decision['remaining'], 0)

    def test_demo_and_other_account_or_platform_do_not_consume_this_account(self):
        self._insert_job(created_at='2026-10-25 09:00:00', demo=1)
        self._insert_job(created_at='2026-10-25 09:00:00', account_id=self.account_id + 1)
        self._insert_job(created_at='2026-10-25 09:00:00', platform='zhihu')
        self.assertEqual(self._decision('2026-10-25T10:00:00Z')['used_24h'], 0)

    def test_retry_replaces_only_own_slot_without_mutating_or_double_counting(self):
        own = self._insert_job(created_at='2026-10-25 08:00:00', status='failed')
        self._insert_job(created_at='2026-10-25 12:00:00', status='need_action')
        new = self._decision('2026-10-25T16:00:00Z')
        retry = self._decision('2026-10-25T16:00:00Z', exclude_job_id=own)
        self.assertFalse(new['allowed'])
        self.assertEqual(new['used_24h'], 2)
        self.assertTrue(retry['allowed'])
        self.assertEqual(retry['used_24h'], 1)
        with closing(self._connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM publish_jobs').fetchone()[0], 2)

    def test_next_interval_ignores_unrelated_old_and_far_future_jobs(self):
        for value in ('2026-09-01 00:00:00', '2026-10-25 11:00:00', '2026-11-01 00:00:00'):
            self._insert_job(created_at=value)
        decision = self._decision('2026-10-25T12:00:00Z')
        self.assertEqual(decision['next_allowed_at'], '2026-10-25T14:00:00+00:00')
        self.assertTrue(self._decision(decision['next_allowed_at'])['allowed'])

    def test_next_rolling_window_ignores_ancient_record_and_is_really_allowed(self):
        for value in ('2026-09-01 00:00:00', '2026-10-25 04:00:00',
                      '2026-10-25 08:00:00', '2026-11-01 00:00:00'):
            self._insert_job(created_at=value)
        decision = self._decision('2026-10-25T12:00:00Z')
        self.assertEqual(decision['reason_code'], 'rolling_limit_reached')
        self.assertEqual(decision['next_allowed_at'], '2026-10-26T04:00:00+00:00')
        self.assertTrue(self._decision(decision['next_allowed_at'])['allowed'])

    def test_next_allowed_must_satisfy_both_interval_and_rolling_constraints(self):
        for value in ('2026-10-25 11:00:00', '2026-10-25 14:00:00'):
            self._insert_job(created_at=value)
        decision = self._decision('2026-10-25T12:00:00Z')
        self.assertEqual(decision['reason_code'], 'minimum_interval')
        self.assertEqual(decision['next_allowed_at'], '2026-10-26T11:00:00+00:00')
        self.assertTrue(self._decision(decision['next_allowed_at'])['allowed'])

    def test_naive_schedule_uses_local_zone_but_database_attempt_is_utc(self):
        self._insert_job(created_at='2026-10-01 00:00:00',
                         publish_at='2026-10-25T12:00:00', started_at='2026-10-25 08:00:00')
        with patch('services.publish_policy_service._local_timezone', return_value=timezone(timedelta(hours=8))):
            decision = self._decision('2026-10-25T12:00:00')
        self.assertEqual(decision['reference_at'], '2026-10-25T04:00:00+00:00')
        self.assertEqual(decision['used_24h'], 1)
        self.assertEqual(decision['attempted_24h'], 0)

    def test_missing_reservation_time_fails_closed_instead_of_releasing_unknown(self):
        self._insert_job(created_at='', status='unknown')
        with self.assertRaisesRegex(ValueError, '缺少可靠的预留时间'):
            self._decision('2026-10-25T12:00:00Z')

    def test_optimized_rolling_count_matches_existing_half_open_policy(self):
        reference = datetime(2026,10,25,12,tzinfo=timezone.utc)
        randomizer = random.Random(20261024)
        offsets = [-48, -24, -23.99999, -12, -6, 0, 0, 3, 12, 23.99999, 24, 48]
        for _ in range(100):
            values = [reference + timedelta(hours=randomizer.choice(offsets))
                      for _ in range(randomizer.randrange(30))]
            points = [*values, reference]
            expected = max((sum(start <= point < start + timedelta(hours=24) for point in points)
                            for start in points if start <= reference < start + timedelta(hours=24)), default=1) - 1
            self.assertEqual(_max_existing_in_rolling_window(values, reference), expected)


if __name__ == "__main__":
    unittest.main()
