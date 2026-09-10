import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from db.createTable import initialize_database
from services.media_account_risk_service import (
    classify_media_account_risk,
    get_active_media_account_risk,
    record_media_account_risk,
    record_publish_job_account_risk,
    resolve_media_account_risks,
    sanitize_risk_evidence,
)
from services.media_account_service import (
    MediaAccountFrozenError,
    check_media_account,
    get_media_accounts_overview,
)


class MediaAccountRiskServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "risk.db"
        self.cookies_dir = self.root / "cookies"
        self.cookies_dir.mkdir()
        initialize_database(self.db_path)
        (self.cookies_dir / "kuaishou.json").write_text("{}", encoding="utf-8")
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (3, 'kuaishou.json', '快手品牌号', 1)
                    """
                ).lastrowid
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('合成项目')"
                ).lastrowid
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, '合成文章', '仅用于离线测试')
                    """,
                    (project_id,),
                ).lastrowid
                self.job_id = conn.execute(
                    """
                    INSERT INTO publish_jobs (
                        article_id, account_id, platform, status, auto_execute,
                        authorization_fingerprint
                    ) VALUES (?, ?, 'kuaishou', 'queued', 1, 'synthetic')
                    """,
                    (article_id, self.account_id),
                ).lastrowid

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_classifies_all_supported_risks_with_deliberate_precedence(self):
        cases = {
            "账号已被永久封禁，无法进入创作者中心": "permanent_ban",
            "违规操作导致账号暂时受到限制": "temporary_restriction",
            "请接收短信验证码完成本人验证": "verification_required",
            "Cookie 已失效，请重新登录": "session_expired",
            "cookie文件不存在，请先完成快手登录": "session_expired",
            "network down: connection failed": "network_error",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    classify_media_account_risk(text)["category"], expected
                )
        self.assertIsNone(classify_media_account_risk("文章标题不能为空"))

    def test_sanitizes_credential_material_before_persisting_evidence(self):
        sanitized = sanitize_risk_evidence(
            "账号已被封禁 cookie=session-value Authorization: Bearer abc.def "
            "api_key=sk-secretvalue"
        )

        self.assertIn("账号已被封禁", sanitized)
        self.assertNotIn("session-value", sanitized)
        self.assertNotIn("abc.def", sanitized)
        self.assertNotIn("sk-secretvalue", sanitized)
        json_sanitized = sanitize_risk_evidence(
            '{"cookie": "json-cookie-value", "access_token": "token-value"}'
        )
        self.assertNotIn("json-cookie-value", json_sanitized)
        self.assertNotIn("token-value", json_sanitized)

    def test_permanent_ban_freezes_account_and_pending_job_idempotently(self):
        occurred_at = datetime(2026, 9, 10, 9, 30, tzinfo=timezone.utc)
        first = record_publish_job_account_risk(
            self.db_path,
            self.job_id,
            "账号已被永久封禁，cookie=do-not-store",
            occurred_at=occurred_at,
        )
        second = record_publish_job_account_risk(
            self.db_path,
            self.job_id,
            "账号已被永久封禁，cookie=do-not-store",
            occurred_at=occurred_at,
        )

        self.assertEqual(first["category"], "permanent_ban")
        self.assertTrue(first["frozen"])
        self.assertEqual(first["stopped_jobs"], 1)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["stopped_jobs"], 0)
        self.assertNotIn("do-not-store", first["evidence"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (self.account_id,)
            ).fetchone()[0]
            job = conn.execute(
                """
                SELECT status, auto_execute, authorization_fingerprint, message
                FROM publish_jobs WHERE id = ?
                """,
                (self.job_id,),
            ).fetchone()
            event_count = conn.execute(
                "SELECT COUNT(*) FROM media_account_risk_events"
            ).fetchone()[0]
        self.assertEqual(status, 0)
        self.assertEqual(job[:3], ("need_action", 0, ""))
        self.assertIn("账号永久封禁", job[3])
        self.assertEqual(event_count, 1)

    def test_network_error_is_visible_but_does_not_freeze_account_or_job(self):
        event = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="network down",
        )

        self.assertEqual(event["category"], "network_error")
        self.assertFalse(event["frozen"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (self.account_id,)
            ).fetchone()[0]
            job_status = conn.execute(
                "SELECT status FROM publish_jobs WHERE id = ?", (self.job_id,)
            ).fetchone()[0]
        self.assertEqual(account_status, 1)
        self.assertEqual(job_status, "queued")

    def test_publish_risk_leaves_current_processing_attempt_for_executor(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE publish_jobs SET status = 'processing' WHERE id = ?",
                    (self.job_id,),
                )
                article_id = conn.execute(
                    "SELECT article_id FROM publish_jobs WHERE id = ?",
                    (self.job_id,),
                ).fetchone()[0]
                queued_job_id = conn.execute(
                    """
                    INSERT INTO publish_jobs (
                        article_id, account_id, platform, status, auto_execute
                    ) VALUES (?, ?, 'kuaishou', 'queued', 1)
                    """,
                    (article_id, self.account_id),
                ).lastrowid

        event = record_publish_job_account_risk(
            self.db_path,
            self.job_id,
            "账号已被永久封禁",
        )

        self.assertEqual(event["stopped_jobs"], 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            processing_status = conn.execute(
                "SELECT status FROM publish_jobs WHERE id = ?", (self.job_id,)
            ).fetchone()[0]
            queued_status = conn.execute(
                "SELECT status FROM publish_jobs WHERE id = ?", (queued_job_id,)
            ).fetchone()[0]
        self.assertEqual(processing_status, "processing")
        self.assertEqual(queued_status, "need_action")

    async def test_active_frozen_risk_blocks_online_checker_and_is_in_overview(self):
        record_media_account_risk(
            self.db_path,
            self.account_id,
            source="publish",
            error_text="账号已被永久封禁",
        )
        checker = AsyncMock(return_value=True)

        with self.assertRaises(MediaAccountFrozenError) as captured:
            await check_media_account(
                self.db_path,
                self.account_id,
                cookies_directory=self.cookies_dir,
                checker=checker,
            )

        checker.assert_not_called()
        self.assertEqual(captured.exception.risk_event["category"], "permanent_ban")
        overview = get_media_accounts_overview(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )
        account = overview["accounts"][0]
        self.assertEqual(account["status"], "permanent_ban")
        self.assertEqual(account["risk_event"]["source"], "publish")
        self.assertEqual(overview["summary"]["needs_action"], 1)

    async def test_successful_check_clears_non_permanent_network_event(self):
        record_media_account_risk(
            self.db_path,
            self.account_id,
            source="login_check",
            error_text="network down",
        )

        account = await check_media_account(
            self.db_path,
            self.account_id,
            cookies_directory=self.cookies_dir,
            checker=AsyncMock(return_value=True),
        )

        self.assertEqual(account["status"], "connected")
        self.assertIsNone(account["risk_event"])
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))

    async def test_false_login_check_records_session_expiry_and_stops_pending_job(self):
        account = await check_media_account(
            self.db_path,
            self.account_id,
            cookies_directory=self.cookies_dir,
            checker=AsyncMock(return_value={"connected": False, "message": "unknown"}),
        )

        self.assertEqual(account["status"], "session_expired")
        self.assertEqual(account["risk_event"]["category"], "session_expired")
        with closing(sqlite3.connect(self.db_path)) as conn:
            job_status = conn.execute(
                "SELECT status FROM publish_jobs WHERE id = ?", (self.job_id,)
            ).fetchone()[0]
        self.assertEqual(job_status, "need_action")

    def test_manual_resolution_does_not_silently_reenable_account(self):
        record_media_account_risk(
            self.db_path,
            self.account_id,
            source="publish",
            error_text="账号已被永久封禁",
        )

        result = resolve_media_account_risks(
            self.db_path,
            self.account_id,
            category="permanent_ban",
        )

        self.assertEqual(result["resolved_count"], 1)
        self.assertTrue(result["requires_login"])
        self.assertFalse(result["account_enabled"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            status = conn.execute(
                "SELECT status FROM user_info WHERE id = ?", (self.account_id,)
            ).fetchone()[0]
        self.assertEqual(status, 0)
        self.assertIsNone(get_active_media_account_risk(self.db_path, self.account_id))


if __name__ == "__main__":
    unittest.main()
