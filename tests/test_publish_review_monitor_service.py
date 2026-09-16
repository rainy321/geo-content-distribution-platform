import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services.media_account_risk_service import (
    get_active_media_account_risk,
    record_media_account_risk,
)
from services.publish_job_service import (
    InvalidPublishJobTransitionError,
    create_publish_job,
    get_publish_job,
    mark_publish_job_awaiting_reconciliation,
    reconcile_publish_job_platform_success,
    retry_publish_job,
    transition_publish_job,
)
from services.publish_review_monitor_service import (
    acknowledge_publish_review_alert,
    get_publish_review_monitor,
    get_publish_review_overview,
    rearm_publish_review_monitor,
    run_publish_review_monitor_tick,
    seed_publish_review_monitors,
)


class PublishReviewMonitorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "review-monitor.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('监测测试品牌')"
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '精确标题监测文章', '本测试不会访问平台。', 'ready')
                    """,
                    (project_id,),
                ).lastrowid
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'synthetic.json', '监测测试账号', 1)
                    """
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def _pending_job(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET status = 'ready' WHERE id = ?",
                    (self.article_id,),
                )
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
            account_id=self.account_id,
        )
        job = transition_publish_job(
            self.db_path,
            job["id"],
            "processing",
            message="平台已接受提交",
            expected_state_version=job["state_version"],
        )
        return mark_publish_job_awaiting_reconciliation(
            self.db_path,
            job["id"],
            message="平台处理中/审核中，禁止重复提交",
            expected_state_version=job["state_version"],
        )

    def test_seeds_only_submitted_unresolved_job_without_changing_job(self):
        job = self._pending_job()
        before = get_publish_job(self.db_path, job["id"])

        inserted = seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T00:00:00Z",
        )
        repeated = seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T00:00:01Z",
        )

        after = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(inserted, 1)
        self.assertEqual(repeated, 0)
        self.assertEqual(after["status"], "need_action")
        self.assertTrue(after["reconciliation_required"])
        self.assertEqual(after["state_version"], before["state_version"])
        self.assertEqual(monitor["state"], "watching")

    def test_seed_resets_only_a_strictly_newer_job_version(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T00:00:00Z",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                replacement_account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'replacement.json', '新版监测账号', 1)
                    """
                ).lastrowid
                conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET state = 'exhausted', attempt_count = 6,
                        max_attempts = 12, next_check_at = NULL,
                        last_checked_at = '2026-09-11T23:59:00Z',
                        last_result = 'not_found', last_message = '旧版本结果',
                        completed_at = '2026-09-12T00:00:30Z'
                    WHERE publish_job_id = ?
                    """,
                    (job["id"],),
                )
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET account_id = ?, state_version = state_version + 1
                    WHERE id = ?
                    """,
                    (replacement_account_id, job["id"]),
                )

        synchronized = seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T00:01:00Z",
            max_attempts=7,
        )
        current = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(synchronized, 1)
        self.assertEqual(monitor["account_id"], replacement_account_id)
        self.assertEqual(monitor["observed_job_version"], current["state_version"])
        self.assertEqual(monitor["state"], "watching")
        self.assertEqual(monitor["attempt_count"], 0)
        self.assertEqual(monitor["max_attempts"], 7)
        self.assertEqual(monitor["next_check_at"], "2026-09-12T00:01:00Z")
        self.assertIsNone(monitor["last_checked_at"])
        self.assertEqual(monitor["last_result"], "")
        self.assertEqual(monitor["last_message"], "")
        self.assertIsNone(monitor["completed_at"])

        # Equal versions are idempotent: a later seed does not erase backoff
        # or operator-visible state from the current version.
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET attempt_count = 2,
                        next_check_at = '2026-09-12T00:10:00Z',
                        last_result = 'processing'
                    WHERE publish_job_id = ?
                    """,
                    (job["id"],),
                )
        self.assertEqual(
            seed_publish_review_monitors(
                self.db_path,
                now="2026-09-12T00:02:00Z",
                max_attempts=3,
            ),
            0,
        )
        same_version = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(same_version["attempt_count"], 2)
        self.assertEqual(same_version["max_attempts"], 7)
        self.assertEqual(same_version["next_check_at"], "2026-09-12T00:10:00Z")

        # A defensive monitor version ahead of the local job must never be
        # overwritten by an older job snapshot.
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET observed_job_version = observed_job_version + 5,
                        state = 'paused', next_check_at = NULL
                    WHERE publish_job_id = ?
                    """,
                    (job["id"],),
                )
        before_older_seed = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(
            seed_publish_review_monitors(
                self.db_path,
                now="2026-09-12T00:03:00Z",
            ),
            0,
        )
        after_older_seed = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(after_older_seed["state"], "paused")
        self.assertEqual(
            after_older_seed["observed_job_version"],
            before_older_seed["observed_job_version"],
        )

    def test_processing_check_uses_durable_backoff_and_never_requeues(self):
        job = self._pending_job()
        calls = []

        def verifier(snapshot):
            calls.append(snapshot)
            return {"state": "processing", "message": "仍在审核中"}

        first = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-12T00:00:00Z",
        )
        too_early = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-12T00:01:59Z",
        )
        due = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-12T00:02:00Z",
        )

        current = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(first["checked_count"], 1)
        self.assertEqual(too_early["checked_count"], 0)
        self.assertEqual(due["checked_count"], 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]["created_at"])
        self.assertIn("started_at", calls[0])
        self.assertTrue(calls[0]["started_at"])
        self.assertEqual(current["status"], "need_action")
        self.assertTrue(current["reconciliation_required"])
        self.assertEqual(monitor["attempt_count"], 2)
        self.assertEqual(monitor["next_check_at"], "2026-09-12T00:07:00Z")

    def test_same_due_time_prioritizes_latest_job(self):
        older_job = self._pending_job()
        newer_job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T00:30:00Z",
        )
        verifier_calls = []

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda snapshot: (
                verifier_calls.append(snapshot["job_id"])
                or {"state": "processing", "message": "仍在审核中"}
            ),
            now="2026-09-12T00:30:00Z",
            limit=1,
        )

        self.assertEqual(result["checked_count"], 1)
        self.assertEqual(verifier_calls, [newer_job["id"]])
        self.assertGreater(newer_job["id"], older_job["id"])
        self.assertEqual(
            get_publish_review_monitor(self.db_path, older_job["id"])["attempt_count"],
            0,
        )

    def test_published_evidence_reconciles_once_and_returns_public_link(self):
        job = self._pending_job()
        verifier_calls = []

        def verifier(snapshot):
            verifier_calls.append(snapshot["job_id"])
            return {
                "state": "published",
                "result_url": "https://zhuanlan.zhihu.com/p/123456789",
                "message": "公开文章列表精确标题匹配",
            }

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-12T01:00:00Z",
        )
        repeated = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-13T01:00:00Z",
        )

        current = get_publish_job(self.db_path, job["id"])
        overview = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(result["results"][0]["state"], "published")
        self.assertEqual(repeated["checked_count"], 0)
        self.assertEqual(verifier_calls, [job["id"]])
        self.assertEqual(current["status"], "success")
        self.assertEqual(
            current["result_url"],
            "https://zhuanlan.zhihu.com/p/123456789",
        )
        self.assertFalse(current["reconciliation_required"])
        self.assertEqual(overview["summary"]["unread_alerts"], 1)
        self.assertEqual(overview["alerts"][0]["category"], "published")
        self.assertEqual(overview["alerts"][0]["result_url"], current["result_url"])

        acknowledged = acknowledge_publish_review_alert(
            self.db_path,
            overview["alerts"][0]["id"],
            acknowledged_at="2026-09-12T01:01:00Z",
        )
        self.assertTrue(acknowledged["acknowledged"])
        self.assertEqual(
            get_publish_review_overview(self.db_path, unread_only=True)["alerts"],
            [],
        )

    def test_success_crash_after_reconcile_is_repaired_without_platform_read(self):
        job = self._pending_job()
        with patch(
            "services.publish_review_monitor_service._finish_monitor",
            side_effect=RuntimeError("synthetic crash after reconcile"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                run_publish_review_monitor_tick(
                    self.db_path,
                    verifier=lambda _snapshot: {
                        "state": "published",
                        "result_url": "https://zhuanlan.zhihu.com/p/333333333",
                        "message": "精确标题公开匹配",
                    },
                    now="2026-09-12T01:30:00Z",
                )

        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "success")
        self.assertEqual(get_publish_review_monitor(self.db_path, job["id"])["state"], "checking")
        verifier_calls = []
        recovered = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda snapshot: verifier_calls.append(snapshot),
            now="2026-09-12T01:31:00Z",
        )
        first = get_publish_review_overview(self.db_path, unread_only=True)
        second = get_publish_review_overview(self.db_path, unread_only=True)

        self.assertEqual(recovered["checked_count"], 0)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(get_publish_review_monitor(self.db_path, job["id"])["state"], "published")
        self.assertEqual(first["summary"]["unread_alerts"], 1)
        self.assertEqual(second["summary"]["unread_alerts"], 1)
        self.assertEqual(first["alerts"][0]["category"], "published")

    def test_human_success_closes_exhausted_monitor_and_superseded_alert(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T01:40:00Z",
            max_attempts=1,
        )
        run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {"state": "not_found"},
            now="2026-09-12T01:40:00Z",
        )
        before = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(
            get_publish_review_monitor(self.db_path, job["id"])["state"],
            "exhausted",
        )
        self.assertEqual(before["alerts"][0]["category"], "monitor_exhausted")

        current = get_publish_job(self.db_path, job["id"])
        reconcile_publish_job_platform_success(
            self.db_path,
            job["id"],
            message="人工核验已发布：已在正确账号后台确认公开",
            result_url="https://zhuanlan.zhihu.com/p/444444444",
            expected_state_version=current["state_version"],
        )

        verifier_calls = []
        tick = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda snapshot: verifier_calls.append(snapshot),
            now="2026-09-12T01:41:00Z",
        )
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        unread = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(tick["checked_count"], 0)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(monitor["state"], "published")
        self.assertEqual(monitor["last_result"], "published")
        self.assertEqual(unread["summary"]["unread_alerts"], 0)
        self.assertEqual(unread["alerts"], [])

    def test_detached_job_account_pauses_old_monitor_without_using_new_login(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T01:50:00Z",
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE publish_jobs SET account_id = NULL WHERE id = ?",
                    (job["id"],),
                )

        verifier_calls = []
        tick = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda snapshot: verifier_calls.append(snapshot),
            now="2026-09-12T01:50:01Z",
        )
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        overview = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(tick["checked_count"], 0)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(monitor["state"], "paused")
        self.assertEqual(monitor["last_result"], "account_binding_changed")
        self.assertIn("不会自动改用新账号", monitor["last_message"])
        self.assertEqual(overview["alerts"][0]["category"], "manual_check")

    def test_explicit_rejection_closes_job_without_any_retry(self):
        job = self._pending_job()

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {
                "state": "rejected",
                "message": "审核未通过：封面不符合规范",
            },
            now="2026-09-12T02:00:00Z",
        )

        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(result["results"][0]["state"], "rejected")
        self.assertEqual(current["status"], "failed")
        self.assertFalse(current["reconciliation_required"])
        self.assertIn("明确未通过", current["message"])
        with self.assertRaisesRegex(
            InvalidPublishJobTransitionError,
            "禁止原稿重试",
        ):
            retry_publish_job(self.db_path, job["id"])

    def test_rejection_crash_after_monitor_finish_backfills_alert_once(self):
        job = self._pending_job()
        with patch(
            "services.publish_review_monitor_service._insert_alert",
            side_effect=RuntimeError("synthetic crash before alert"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                run_publish_review_monitor_tick(
                    self.db_path,
                    verifier=lambda _snapshot: {
                        "state": "rejected",
                        "message": "平台明确显示审核未通过",
                    },
                    now="2026-09-12T02:30:00Z",
                )

        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "failed")
        self.assertEqual(get_publish_review_monitor(self.db_path, job["id"])["state"], "rejected")
        first = get_publish_review_overview(self.db_path, unread_only=True)
        second = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(first["summary"]["unread_alerts"], 1)
        self.assertEqual(second["summary"]["unread_alerts"], 1)
        self.assertEqual(first["alerts"][0]["category"], "rejected")

    def test_account_risk_freezes_monitor_and_alert_is_deduplicated(self):
        job = self._pending_job()

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {
                "state": "risk",
                "message": "账号已被永久封禁",
                "evidence": "平台安全提示：账号已被永久封禁",
            },
            now="2026-09-12T03:00:00Z",
        )
        # Reading overview multiple times must not manufacture duplicate alerts.
        first = get_publish_review_overview(self.db_path, unread_only=True)
        second = get_publish_review_overview(self.db_path, unread_only=True)

        risk = get_active_media_account_risk(self.db_path, self.account_id)
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(result["results"][0]["state"], "action_required")
        self.assertEqual(risk["category"], "permanent_ban")
        self.assertEqual(monitor["state"], "action_required")
        self.assertEqual(first["summary"]["unread_alerts"], 1)
        self.assertEqual(second["summary"]["unread_alerts"], 1)
        self.assertEqual(first["alerts"][0]["severity"], "critical")

    def test_relogin_closes_old_risk_alert_without_deleting_audit_history(self):
        risk = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="平台安全中心提示：该账号已被永久封禁",
            occurred_at="2026-09-12T03:10:00Z",
        )

        before = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(risk["category"], "permanent_ban")
        self.assertEqual(before["summary"]["unread_alerts"], 1)
        self.assertEqual(before["alerts"][0]["category"], "account_risk")
        self.assertEqual(before["alerts"][0]["severity"], "critical")

        # A successful re-login creates a new credential generation.  The old
        # ban remains in the audit table, but must not be treated as a current
        # risk or an unread red alert for the new session.
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE user_info
                    SET filePath = 'successful-relogin.json', status = 1,
                        credential_epoch = credential_epoch + 1
                    WHERE id = ?
                    """,
                    (self.account_id,),
                )

        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        current = get_publish_review_overview(self.db_path)
        unread = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(current["summary"]["unread_alerts"], 0)
        self.assertEqual(unread["summary"]["unread_alerts"], 0)
        self.assertFalse(
            any(
                alert["category"] == "account_risk"
                and alert["severity"] in {"critical", "error"}
                for alert in current["alerts"]
            )
        )
        self.assertEqual(unread["alerts"], [])

        with closing(sqlite3.connect(self.db_path)) as conn:
            history = conn.execute(
                """
                SELECT event_key, category, severity, acknowledged_at
                FROM publish_review_alerts
                WHERE event_key = ?
                """,
                (f"account-risk:{risk['id']}",),
            ).fetchone()
        self.assertIsNotNone(history)
        self.assertEqual(history[0], f"account-risk:{risk['id']}")
        self.assertEqual(history[1], "account_risk")
        self.assertEqual(history[2], "critical")
        self.assertIsNotNone(history[3])

    def test_old_verifier_risk_cannot_freeze_new_login_generation(self):
        job = self._pending_job()
        snapshots = []

        def verifier(snapshot):
            snapshots.append(snapshot)
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE user_info
                        SET filePath = 'fresh-session.json', status = 1,
                            credential_epoch = credential_epoch + 1
                        WHERE id = ?
                        """,
                        (self.account_id,),
                    )
            return {
                "state": "risk",
                "evidence": "旧 Cookie 页面提示账号已被永久封禁",
            }

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now="2026-09-12T03:20:00Z",
        )

        self.assertEqual(snapshots[0]["account_credential_epoch"], 1)
        self.assertTrue(result["results"][0]["stale_account_result_discarded"])
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "need_action")
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(monitor["state"], "watching")
        self.assertEqual(monitor["attempt_count"], 0)
        self.assertNotIn("observed_account_epoch", monitor)
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status, account_epoch, observed_epoch = conn.execute(
                """
                SELECT u.status, u.credential_epoch, m.observed_account_epoch
                FROM user_info AS u
                JOIN publish_review_monitors AS m ON m.account_id = u.id
                WHERE u.id = ? AND m.publish_job_id = ?
                """,
                (self.account_id, job["id"]),
            ).fetchone()
            alert_count = conn.execute(
                "SELECT COUNT(*) FROM publish_review_alerts"
            ).fetchone()[0]
        self.assertEqual((account_status, account_epoch, observed_epoch), (1, 2, 2))
        self.assertEqual(alert_count, 0)

    def test_job_version_and_account_epoch_are_rechecked_in_risk_write_transaction(self):
        job = self._pending_job()
        from services import publish_review_monitor_service as monitor_service

        original_record_risk = monitor_service.record_media_account_risk
        replaced_versions = []

        def replace_job_immediately_before_risk_write(*args, **kwargs):
            # This is the exact former race window: all read-only snapshot
            # checks have passed, but the risk writer has not acquired its
            # transaction yet.  A newer publish attempt wins first.
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    before = conn.execute(
                        "SELECT state_version FROM publish_jobs WHERE id = ?",
                        (job["id"],),
                    ).fetchone()[0]
                    conn.execute(
                        """
                        UPDATE publish_jobs
                        SET state_version = state_version + 1,
                            message = '新版发布尝试等待核验'
                        WHERE id = ? AND state_version = ?
                        """,
                        (job["id"], before),
                    )
                    replaced_versions.append(before + 1)
            return original_record_risk(*args, **kwargs)

        with patch(
            "services.publish_review_monitor_service.record_media_account_risk",
            side_effect=replace_job_immediately_before_risk_write,
        ):
            result = run_publish_review_monitor_tick(
                self.db_path,
                verifier=lambda _snapshot: {
                    "state": "risk",
                    "evidence": "旧任务页面提示账号已被永久封禁",
                },
                now="2026-09-12T03:25:00Z",
            )

        current = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(replaced_versions, [job["state_version"] + 1])
        self.assertTrue(result["results"][0]["stale_result_discarded"])
        self.assertEqual(current["state_version"], replaced_versions[0])
        self.assertEqual(current["status"], "need_action")
        self.assertEqual(current["message"], "新版发布尝试等待核验")
        self.assertEqual(monitor["state"], "watching")
        self.assertEqual(monitor["attempt_count"], 0)
        self.assertEqual(monitor["observed_job_version"], current["state_version"])
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?",
                (self.account_id,),
            ).fetchone()[0]
            active_risk_count = conn.execute(
                "SELECT COUNT(*) FROM media_account_risk_events WHERE active = 1"
            ).fetchone()[0]
            alert_count = conn.execute(
                "SELECT COUNT(*) FROM publish_review_alerts"
            ).fetchone()[0]
        self.assertEqual(account_status, 1)
        self.assertEqual(active_risk_count, 0)
        self.assertEqual(alert_count, 0)

    def test_relogin_after_risk_write_prevents_old_monitor_state_and_alert(self):
        job = self._pending_job()
        from services import publish_review_monitor_service as monitor_service

        original_record_risk = monitor_service.record_media_account_risk
        recorded_risk_ids = []

        def record_old_risk_then_relogin(*args, **kwargs):
            # The risk writer legitimately wins its first transaction.  A
            # successful login then advances the credential generation before
            # monitor finalization and alert creation.
            risk = original_record_risk(*args, **kwargs)
            recorded_risk_ids.append(risk["id"])
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE user_info
                        SET filePath = 'newest-session.json', status = 1,
                            credential_epoch = credential_epoch + 1
                        WHERE id = ?
                        """,
                        (self.account_id,),
                    )
            return risk

        with patch(
            "services.publish_review_monitor_service.record_media_account_risk",
            side_effect=record_old_risk_then_relogin,
        ):
            result = run_publish_review_monitor_tick(
                self.db_path,
                verifier=lambda _snapshot: {
                    "state": "risk",
                    "evidence": "旧会话页面提示账号已被永久封禁",
                },
                now="2026-09-12T03:25:30Z",
            )

        monitor = get_publish_review_monitor(self.db_path, job["id"])
        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(len(recorded_risk_ids), 1)
        self.assertTrue(result["results"][0]["stale_account_result_discarded"])
        self.assertEqual(current["status"], "need_action")
        self.assertEqual(current["state_version"], job["state_version"])
        self.assertEqual(monitor["state"], "watching")
        self.assertEqual(monitor["attempt_count"], 0)
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status, account_epoch, observed_epoch = conn.execute(
                """
                SELECT u.status, u.credential_epoch, m.observed_account_epoch
                FROM user_info AS u
                JOIN publish_review_monitors AS m ON m.account_id = u.id
                WHERE u.id = ? AND m.publish_job_id = ?
                """,
                (self.account_id, job["id"]),
            ).fetchone()
            alert_count = conn.execute(
                "SELECT COUNT(*) FROM publish_review_alerts"
            ).fetchone()[0]
        self.assertEqual((account_status, account_epoch, observed_epoch), (1, 2, 2))
        self.assertEqual(alert_count, 0)

    def test_pause_monitor_ignores_old_risk_when_relogin_wins_before_transaction(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T03:26:00Z",
        )
        risk = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="平台安全中心提示：该账号已被永久封禁",
            occurred_at="2026-09-12T03:26:01Z",
        )
        self.assertEqual(risk["category"], "permanent_ban")

        from services import publish_review_monitor_service as monitor_service

        original_connect = monitor_service._connect
        relogin_injected = []

        def connect_after_relogin(database_path):
            # Old code fetched active risks before opening its write
            # transaction.  Injecting the re-login here makes that stale read
            # deterministic; the transactional join must instead see epoch 2.
            if not relogin_injected:
                with closing(sqlite3.connect(self.db_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            UPDATE user_info
                            SET filePath = 'new-login.json', status = 1,
                                credential_epoch = credential_epoch + 1
                            WHERE id = ?
                            """,
                            (self.account_id,),
                        )
                relogin_injected.append(True)
            return original_connect(database_path)

        with patch(
            "services.publish_review_monitor_service._connect",
            side_effect=connect_after_relogin,
        ):
            paused = monitor_service._pause_ineligible_account_monitors(
                self.db_path,
                now=datetime(2026, 9, 12, 3, 26, 2, tzinfo=timezone.utc),
            )

        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(relogin_injected, [True])
        self.assertEqual(paused, 0)
        self.assertEqual(monitor["state"], "watching")
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status, account_epoch = conn.execute(
                "SELECT status, credential_epoch FROM user_info WHERE id = ?",
                (self.account_id,),
            ).fetchone()
            alert_count = conn.execute(
                "SELECT COUNT(*) FROM publish_review_alerts"
            ).fetchone()[0]
        self.assertEqual((account_status, account_epoch), (1, 2))
        self.assertEqual(alert_count, 0)

    def test_risk_alert_backfill_ignores_old_epoch_when_relogin_wins_transaction(self):
        risk = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="平台安全中心提示：该账号已被永久封禁",
            occurred_at="2026-09-12T03:27:00Z",
        )
        self.assertEqual(risk["category"], "permanent_ban")

        from services import publish_review_monitor_service as monitor_service

        original_connect = monitor_service._connect
        relogin_injected = []

        def connect_after_relogin(database_path):
            # Old code first materialized a risk dict on another connection.
            # Advancing the epoch immediately before its alert transaction
            # made that dict stale while still allowing a critical alert.
            if not relogin_injected:
                with closing(sqlite3.connect(self.db_path)) as conn:
                    with conn:
                        conn.execute(
                            """
                            UPDATE user_info
                            SET filePath = 'alert-safe-session.json', status = 1,
                                credential_epoch = credential_epoch + 1
                            WHERE id = ?
                            """,
                            (self.account_id,),
                        )
                relogin_injected.append(True)
            return original_connect(database_path)

        with patch(
            "services.publish_review_monitor_service._connect",
            side_effect=connect_after_relogin,
        ):
            inserted = monitor_service._sync_account_risk_alerts(self.db_path)

        self.assertEqual(relogin_injected, [True])
        self.assertEqual(inserted, 0)
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status, account_epoch = conn.execute(
                "SELECT status, credential_epoch FROM user_info WHERE id = ?",
                (self.account_id,),
            ).fetchone()
            alert_count = conn.execute(
                "SELECT COUNT(*) FROM publish_review_alerts"
            ).fetchone()[0]
        self.assertEqual((account_status, account_epoch), (1, 2))
        self.assertEqual(alert_count, 0)

    def test_preexisting_frozen_risk_never_calls_platform_verifier(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T03:30:00Z",
        )
        risk = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="平台安全中心提示：该账号已被永久封禁",
            occurred_at="2026-09-12T03:31:00Z",
        )
        verifier_calls = []

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda snapshot: verifier_calls.append(snapshot),
            now="2026-09-12T03:32:00Z",
        )

        monitor = get_publish_review_monitor(self.db_path, job["id"])
        overview = get_publish_review_overview(self.db_path, unread_only=True)
        self.assertEqual(risk["category"], "permanent_ban")
        self.assertEqual(result["checked_count"], 0)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(monitor["state"], "action_required")
        self.assertEqual(monitor["last_result"], "permanent_ban")
        self.assertEqual(overview["summary"]["unread_alerts"], 1)
        self.assertEqual(overview["alerts"][0]["category"], "account_risk")
        self.assertEqual(overview["alerts"][0]["severity"], "critical")

        with self.assertRaisesRegex(Exception, "账号仍处于"):
            rearm_publish_review_monitor(
                self.db_path,
                job["id"],
                now="2026-09-12T03:33:00Z",
            )

    def test_risk_recorded_after_claim_is_rechecked_before_verifier(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T03:40:00Z",
        )
        from services import publish_review_monitor_service as monitor_service

        original_claim = monitor_service._claim_due_monitors

        def claim_then_freeze(*args, **kwargs):
            snapshots = original_claim(*args, **kwargs)
            record_media_account_risk(
                self.db_path,
                self.account_id,
                source="login_check",
                error_text="平台要求本人完成安全验证",
                occurred_at="2026-09-12T03:40:01Z",
            )
            return snapshots

        verifier_calls = []
        with patch(
            "services.publish_review_monitor_service._claim_due_monitors",
            side_effect=claim_then_freeze,
        ):
            result = run_publish_review_monitor_tick(
                self.db_path,
                verifier=lambda snapshot: verifier_calls.append(snapshot),
                now="2026-09-12T03:40:00Z",
            )

        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(result["checked_count"], 1)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(monitor["state"], "action_required")
        self.assertEqual(monitor["last_result"], "verification_required")

    def test_monitor_exhaustion_is_bounded_and_can_be_explicitly_rearmed(self):
        job = self._pending_job()
        seed_publish_review_monitors(
            self.db_path,
            now="2026-09-12T04:00:00Z",
            max_attempts=1,
        )
        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {"state": "not_found"},
            now="2026-09-12T04:00:00Z",
        )

        current = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        self.assertEqual(result["results"][0]["state"], "exhausted")
        self.assertEqual(monitor["state"], "exhausted")
        self.assertEqual(current["status"], "need_action")
        self.assertTrue(current["reconciliation_required"])

        rearmed = rearm_publish_review_monitor(
            self.db_path,
            job["id"],
            now="2026-09-12T05:00:00Z",
        )
        self.assertEqual(rearmed["state"], "watching")
        self.assertEqual(rearmed["attempt_count"], 0)
        self.assertEqual(rearmed["next_check_at"], "2026-09-12T05:00:00Z")

    def test_old_verifier_result_cannot_overwrite_new_version_or_its_backoff(self):
        job = self._pending_job()
        nested_results = []

        def old_version_verifier(snapshot):
            old_version = snapshot["state_version"]
            with closing(sqlite3.connect(self.db_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE publish_jobs
                        SET state_version = state_version + 1,
                            message = '新版待核验尝试'
                        WHERE id = ? AND state_version = ?
                        """,
                        (snapshot["job_id"], old_version),
                    )

            # Simulate another scheduler tick claiming the newer version and
            # establishing its own durable backoff before the old verifier
            # returns.  The outer result must preserve this exact state.
            nested_results.append(
                run_publish_review_monitor_tick(
                    self.db_path,
                    verifier=lambda _new_snapshot: {
                        "state": "processing",
                        "message": "新版仍在审核中",
                    },
                    now="2026-09-12T05:30:01Z",
                )
            )
            return {
                "state": "published",
                "result_url": "https://zhuanlan.zhihu.com/p/111111111",
                "message": "旧版本页面命中",
            }

        with patch(
            "services.publish_review_monitor_service.reconcile_publish_job_platform_success"
        ) as reconcile_mock:
            outer = run_publish_review_monitor_tick(
                self.db_path,
                verifier=old_version_verifier,
                now="2026-09-12T05:30:00Z",
            )

        current = get_publish_job(self.db_path, job["id"])
        monitor = get_publish_review_monitor(self.db_path, job["id"])
        overview = get_publish_review_overview(self.db_path, unread_only=True)
        reconcile_mock.assert_not_called()
        self.assertEqual(nested_results[0]["checked_count"], 1)
        self.assertEqual(outer["results"][0]["stale_result_discarded"], True)
        self.assertEqual(current["status"], "need_action")
        self.assertTrue(current["reconciliation_required"])
        self.assertEqual(current["message"], "新版待核验尝试")
        self.assertEqual(monitor["observed_job_version"], current["state_version"])
        self.assertEqual(monitor["state"], "watching")
        self.assertEqual(monitor["attempt_count"], 1)
        self.assertEqual(monitor["last_result"], "processing")
        self.assertEqual(monitor["last_message"], "新版仍在审核中")
        self.assertEqual(monitor["next_check_at"], "2026-09-12T05:32:01Z")
        self.assertEqual(overview["summary"]["unread_alerts"], 0)

        # Once the current version is due, its own evidence is allowed to
        # reconcile exactly once.
        current_result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {
                "state": "published",
                "result_url": "https://zhuanlan.zhihu.com/p/222222222",
                "message": "新版审核通过",
            },
            now="2026-09-12T05:32:01Z",
        )
        self.assertEqual(current_result["results"][0]["state"], "published")
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "success")

    def test_stale_check_does_not_overwrite_human_reconciliation(self):
        job = self._pending_job()

        def verifier(snapshot):
            current = get_publish_job(self.db_path, snapshot["job_id"])
            reconcile_publish_job_platform_success(
                self.db_path,
                snapshot["job_id"],
                message="人工已先行核验",
                result_url="https://zhuanlan.zhihu.com/p/987654321",
                expected_state_version=current["state_version"],
            )
            return {"state": "processing", "message": "旧快照仍显示审核中"}

        result = run_publish_review_monitor_tick(
            self.db_path,
            verifier=verifier,
            now=datetime(2026, 9, 12, 6, tzinfo=timezone.utc),
        )

        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(result["results"][0]["state"], "published")
        self.assertEqual(current["status"], "success")
        self.assertEqual(current["message"], "人工已先行核验")
        self.assertEqual(
            current["result_url"],
            "https://zhuanlan.zhihu.com/p/987654321",
        )


if __name__ == "__main__":
    unittest.main()
