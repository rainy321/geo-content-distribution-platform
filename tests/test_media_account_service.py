import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from db.createTable import initialize_database
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
    check_media_account,
    get_media_accounts_overview,
)
from services.publish_policy_service import evaluate_publish_reservation


class MediaAccountServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "accounts.db"
        self.cookies_dir = self.root / "cookies"
        self.cookies_dir.mkdir()
        initialize_database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_account(
        self,
        *,
        platform_type=9,
        file_path="account.json",
        name="运营账号",
        status=0,
        last_checked_at=None,
    ):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO user_info (
                        type, filePath, userName, status, last_checked_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (platform_type, file_path, name, status, last_checked_at),
                )
        return cursor.lastrowid

    def _insert_reservation(self, account_id, *, created_at, publish_at=None,
                            started_at=None, status='scheduled', demo=0):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            project_id = conn.execute("INSERT INTO projects(name) VALUES ('synthetic quota project')").lastrowid
            article_id = conn.execute("INSERT INTO articles(project_id,title,content,status) VALUES (?,'synthetic quota','body','ready')", (project_id,)).lastrowid
            if status == 'unknown':
                # A legacy job can have a state the current schema cannot create.
                conn.execute('PRAGMA ignore_check_constraints = ON')
            return conn.execute('''INSERT INTO publish_jobs(article_id,account_id,platform,status,demo,
                created_at,publish_at,started_at) VALUES(?,?,'toutiao',?,?,?,?,?)''',
                (article_id, account_id, status, demo, created_at, publish_at, started_at)).lastrowid

    def _overview_policy(self, account_id, reference):
        overview = get_media_accounts_overview(self.db_path, cookies_directory=self.cookies_dir,
                                               publish_at=reference)
        return next(account['publish_policy'] for account in overview['accounts'] if account['id'] == account_id)

    async def test_overview_groups_accounts_and_does_not_trust_missing_cookie(self):
        (self.cookies_dir / "zhihu.json").write_text("{}", encoding="utf-8")
        self._insert_account(
            platform_type=9,
            file_path="zhihu.json",
            name="知乎主号",
            status=1,
            last_checked_at="2026-08-31 12:00:00",
        )
        self._insert_account(
            platform_type=7,
            file_path="missing.json",
            name="头条主号",
            status=1,
        )

        overview = get_media_accounts_overview(
            self.db_path,
            cookies_directory=self.cookies_dir,
        )

        self.assertEqual(
            overview["summary"],
            {"total": 2, "connected": 1, "needs_action": 1, "checking": 0},
        )
        self.assertEqual(len(overview["platforms"]), 10)
        self.assertEqual(overview["platforms"][-1]["key"], "tiktok")
        zhihu = next(item for item in overview["platforms"] if item["key"] == "zhihu")
        toutiao = next(item for item in overview["platforms"] if item["key"] == "toutiao")
        self.assertEqual(zhihu["connected_count"], 1)
        self.assertEqual(zhihu["safe_daily_new"], 1)
        self.assertEqual(zhihu["safe_daily_stable"], 2)
        self.assertEqual(zhihu["accounts"][0]["status"], "connected")
        self.assertEqual(
            zhihu["accounts"][0]["publish_policy"]["remaining_new"],
            1,
        )
        self.assertEqual(toutiao["accounts"][0]["status"], "missing_cookie")
        self.assertNotIn("file_path", zhihu["accounts"][0])
        self.assertTrue(
            any(
                item["key"] == "owned_web"
                for item in overview["planned_platforms"]
            )
        )

    async def test_check_updates_status_and_last_checked_time(self):
        (self.cookies_dir / "zhihu.json").write_text("{}", encoding="utf-8")
        account_id = self._insert_account(
            file_path="zhihu.json",
            status=0,
        )
        checker = AsyncMock(return_value=True)
        checked_at = datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc)

        account = await check_media_account(
            self.db_path,
            account_id,
            cookies_directory=self.cookies_dir,
            checker=checker,
            checked_at=checked_at,
        )

        checker.assert_awaited_once_with(9, "zhihu.json")
        self.assertEqual(account["status"], "connected")
        self.assertEqual(account["last_checked_at"], "2026-08-31 14:30:00")
        self.assertEqual(account["check_message"], "登录状态正常")

    async def test_missing_cookie_is_actionable_without_calling_platform(self):
        account_id = self._insert_account(file_path="missing.json", status=1)
        checker = AsyncMock(return_value=True)

        account = await check_media_account(
            self.db_path,
            account_id,
            cookies_directory=self.cookies_dir,
            checker=checker,
        )

        checker.assert_not_called()
        self.assertEqual(account["status"], "missing_cookie")
        self.assertIn("Cookie 文件不存在", account["check_message"])

    async def test_checker_failure_does_not_overwrite_last_known_status(self):
        (self.cookies_dir / "zhihu.json").write_text("{}", encoding="utf-8")
        account_id = self._insert_account(file_path="zhihu.json", status=1)
        checker = AsyncMock(side_effect=RuntimeError("network down"))

        with self.assertRaisesRegex(MediaAccountCheckError, "检测失败"):
            await check_media_account(
                self.db_path,
                account_id,
                cookies_directory=self.cookies_dir,
                checker=checker,
            )

        with closing(sqlite3.connect(self.db_path)) as conn:
            status, last_checked_at = conn.execute(
                "SELECT status, last_checked_at FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()
        self.assertEqual(status, 1)
        self.assertIsNone(last_checked_at)

    async def test_unknown_account_is_rejected(self):
        with self.assertRaisesRegex(MediaAccountNotFoundError, "不存在"):
            await check_media_account(
                self.db_path,
                999,
                cookies_directory=self.cookies_dir,
                checker=AsyncMock(),
            )

    async def test_quota_overview_uses_actual_start_of_old_schedule_and_matches_authority(self):
        account = self._insert_account(platform_type=7)
        self._insert_reservation(account, created_at='2026-09-01 00:00:00',
                                 publish_at='2026-09-02T00:00:00Z',
                                 started_at='2026-10-25 11:00:00', status='need_action')
        reference = '2026-10-25T12:00:00Z'
        policy = self._overview_policy(account, reference)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            decision = evaluate_publish_reservation(conn, account_id=account, platform='toutiao', publish_at=reference)
        self.assertEqual(policy['reserved_24h'], decision['used_24h'])
        self.assertEqual(policy['remaining_new'], decision['remaining'])
        self.assertEqual(policy['allowed'], decision['allowed'])
        self.assertEqual(policy['reason_code'], 'minimum_interval')
        self.assertEqual(policy['attempted_24h'], 1)
        self.assertEqual(policy['last_attempt_at'], '2026-10-25T11:00:00+00:00')
        self.assertEqual(policy['next_allowed_at'], decision['next_allowed_at'])

    async def test_future_reservation_does_not_block_now_but_applies_to_selected_schedule(self):
        account = self._insert_account(platform_type=7)
        self._insert_reservation(account, created_at='2026-10-25 08:00:00',
                                 publish_at='2026-10-30T12:00:00Z')
        current = self._overview_policy(account, '2026-10-25T12:00:00Z')
        future = self._overview_policy(account, '2026-10-30T12:00:00Z')
        self.assertTrue(current['allowed'])
        self.assertEqual((current['reserved_24h'], current['remaining_new']), (0, 2))
        self.assertFalse(future['allowed'])
        self.assertEqual((future['reserved_24h'], future['remaining_new']), (1, 0))
        self.assertEqual(future['attempted_24h'], 0)

    async def test_failed_unknown_and_retry_same_row_still_reserve_once(self):
        account = self._insert_account(platform_type=7)
        job = self._insert_reservation(account, created_at='2026-10-25 08:00:00', status='failed')
        self._insert_reservation(account, created_at='2026-10-25 12:00:00', status='unknown')
        failed = self._overview_policy(account, '2026-10-25T16:00:00Z')
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE publish_jobs SET status='queued' WHERE id=?", (job,))
        queued = self._overview_policy(account, '2026-10-25T16:00:00Z')
        self.assertEqual(failed['reserved_24h'], 2)
        self.assertEqual(queued['reserved_24h'], 2)
        self.assertEqual(queued['remaining_new'], 0)
        self.assertFalse(queued['allowed'])

    async def test_empty_overview_validates_and_echoes_one_reference(self):
        overview = get_media_accounts_overview(self.db_path, cookies_directory=self.cookies_dir,
                                               publish_at='2026-10-25T12:00:00+08:00')
        self.assertEqual(overview['reference_at'], '2026-10-25T04:00:00+00:00')
        self.assertEqual(overview['reference_mode'], 'scheduled')
        self.assertEqual(overview['accounts'], [])
        current = get_media_accounts_overview(self.db_path, cookies_directory=self.cookies_dir,
                                              now=datetime(2026,10,25,12,tzinfo=timezone.utc))
        self.assertEqual(current['reference_at'], '2026-10-25T12:00:00+00:00')
        self.assertEqual(current['reference_mode'], 'now')
        with self.assertRaises(ValueError):
            get_media_accounts_overview(self.db_path, cookies_directory=self.cookies_dir, publish_at='invalid')

    async def test_all_account_views_have_the_same_reference_without_mutating_jobs(self):
        first = self._insert_account(platform_type=7)
        second = self._insert_account(platform_type=9)
        self._insert_reservation(first, created_at='2026-10-25 08:00:00', status='failed')
        with closing(sqlite3.connect(self.db_path)) as conn:
            before = conn.execute('SELECT * FROM publish_jobs').fetchall()
        overview = get_media_accounts_overview(self.db_path, cookies_directory=self.cookies_dir,
                                               publish_at='2026-10-25T16:00:00Z')
        for account in overview['accounts']:
            self.assertEqual(account['publish_policy']['reference_at'], overview['reference_at'])
        for platform in overview['platforms']:
            for account in platform['accounts']:
                self.assertEqual(account['publish_policy']['reference_at'], overview['reference_at'])
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM publish_jobs').fetchall(), before)


class MediaAccountSchemaMigrationTests(unittest.TestCase):
    def test_adds_last_checked_at_to_legacy_user_info_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE user_info (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            type INTEGER NOT NULL,
                            filePath TEXT NOT NULL,
                            userName TEXT NOT NULL,
                            status INTEGER DEFAULT 0
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO user_info (type, filePath, userName, status)
                        VALUES (9, 'legacy.json', '旧账号', 1)
                        """
                    )

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(user_info)")
                }
                row = conn.execute(
                    "SELECT userName, status, last_checked_at FROM user_info"
                ).fetchone()
            self.assertIn("last_checked_at", columns)
            self.assertEqual(row, ("旧账号", 1, None))


if __name__ == "__main__":
    unittest.main()
