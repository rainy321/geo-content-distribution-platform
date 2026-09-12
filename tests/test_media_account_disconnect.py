import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import (
    LoginStatusQueue,
    active_account_checks,
    active_queues,
    active_queues_lock,
    app,
)
from services.media_account_service import (
    DISCONNECTED_ACCOUNT_STATUS,
    MediaAccountBusyError,
    MediaAccountInvalidCookiePathError,
    MediaAccountNotFoundError,
    check_media_account,
    cleanup_disconnected_account_files,
    disconnect_media_account,
)


class MediaAccountDisconnectServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "geo.db"
        self.cookies_directory = self.root / "cookies"
        self.cookies_directory.mkdir()
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _insert_account(self, filename="account.json"):
        with closing(sqlite3.connect(self.database_path)) as connection:
            account_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (9, ?, '本机测试账号', 1)
                """,
                (filename,),
            ).lastrowid
            connection.commit()
        return account_id

    def _insert_job(self, account_id, *, status="scheduled"):
        with closing(sqlite3.connect(self.database_path)) as connection:
            project_id = connection.execute(
                "INSERT INTO projects (name) VALUES ('合成测试项目')"
            ).lastrowid
            article_id = connection.execute(
                """
                INSERT INTO articles (project_id, title, content, status)
                VALUES (?, '合成文章', '仅本地测试', 'ready')
                """,
                (project_id,),
            ).lastrowid
            job_id = connection.execute(
                """
                INSERT INTO publish_jobs (
                    article_id, account_id, platform, status,
                    authorization_fingerprint, auto_execute
                ) VALUES (?, ?, 'zhihu', ?, 'prior-confirmation', 1)
                """,
                (article_id, account_id, status),
            ).lastrowid
            connection.commit()
        return job_id

    def test_disconnect_removes_cookie_and_revokes_bound_job_authorization(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text('{"cookies": []}', encoding="utf-8")
        job_id = self._insert_job(account_id)

        result = disconnect_media_account(
            self.database_path,
            account_id,
            cookies_directory=self.cookies_directory,
        )

        self.assertEqual(result["jobs_unbound"], 1)
        self.assertTrue(result["cookie_removed"])
        self.assertFalse(result["cleanup_pending"])
        self.assertFalse(cookie_file.exists())
        with closing(sqlite3.connect(self.database_path)) as connection:
            account = connection.execute(
                "SELECT status, filePath, userName FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()
            job = connection.execute(
                """
                SELECT account_id, status, authorization_fingerprint,
                       auto_execute, state_version, message
                FROM publish_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        self.assertEqual(
            account,
            (DISCONNECTED_ACCOUNT_STATUS, "", "本机测试账号"),
        )
        self.assertEqual(job[:5], (None, "need_action", "", 0, 1))
        self.assertIn("重新选择账号", job[5])

    def test_path_traversal_is_rejected_without_touching_account_or_file(self):
        outside_file = self.root / "outside.json"
        outside_file.write_text("private", encoding="utf-8")
        account_id = self._insert_account("../outside.json")

        with self.assertRaises(MediaAccountInvalidCookiePathError):
            disconnect_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
            )

        self.assertEqual(outside_file.read_text(encoding="utf-8"), "private")
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM user_info WHERE id = ?", (account_id,)
                ).fetchone()[0],
                1,
            )

    def test_missing_cookie_still_disables_stale_database_binding(self):
        account_id = self._insert_account("missing.json")

        result = disconnect_media_account(
            self.database_path,
            account_id,
            cookies_directory=self.cookies_directory,
        )

        self.assertTrue(result["cookie_missing"])
        self.assertFalse(result["cookie_removed"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status, filePath FROM user_info WHERE id = ?",
                    (account_id,),
                ).fetchone(),
                (DISCONNECTED_ACCOUNT_STATUS, ""),
            )

    def test_database_failure_happens_before_cookie_is_touched(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text("private", encoding="utf-8")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_account_disconnect
                BEFORE UPDATE OF status ON user_info
                WHEN NEW.status = -2
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic disconnect failure');
                END
                """
            )
            connection.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            disconnect_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
            )

        self.assertEqual(cookie_file.read_text(encoding="utf-8"), "private")
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT status, filePath FROM user_info WHERE id = ?",
                    (account_id,),
                ).fetchone(),
                (1, "account.json"),
            )

    def test_unlink_failure_is_reported_and_later_cleanup_is_bounded_to_root(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text("private", encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=PermissionError("in use")):
            result = disconnect_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
            )

        self.assertTrue(result["cleanup_pending"])
        self.assertTrue(cookie_file.exists())
        active_account_id = self._insert_account("active.json")
        active_cookie = self.cookies_directory / "active.json"
        active_cookie.write_text("active", encoding="utf-8")

        cleanup = cleanup_disconnected_account_files(
            self.database_path,
            self.cookies_directory,
        )

        self.assertEqual(cleanup, {"removed": 1, "remaining": 0})
        self.assertFalse(cookie_file.exists())
        self.assertTrue(active_cookie.exists())
        with closing(sqlite3.connect(self.database_path)) as connection:
            disconnected = connection.execute(
                "SELECT status, filePath FROM user_info WHERE id = ?",
                (account_id,),
            ).fetchone()
            active = connection.execute(
                "SELECT status, filePath FROM user_info WHERE id = ?",
                (active_account_id,),
            ).fetchone()
        self.assertEqual(disconnected, (DISCONNECTED_ACCOUNT_STATUS, ""))
        self.assertEqual(active, (1, "active.json"))

    def test_completed_history_keeps_account_audit_label(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text("private", encoding="utf-8")
        success_job_id = self._insert_job(account_id, status="success")
        failed_job_id = self._insert_job(account_id, status="failed")

        result = disconnect_media_account(
            self.database_path,
            account_id,
            cookies_directory=self.cookies_directory,
        )

        self.assertEqual(result["jobs_unbound"], 0)
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT j.id, j.account_id, u.userName
                FROM publish_jobs AS j
                LEFT JOIN user_info AS u ON u.id = j.account_id
                WHERE j.id IN (?, ?)
                ORDER BY j.id
                """,
                (success_job_id, failed_job_id),
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (success_job_id, account_id, "本机测试账号"),
                (failed_job_id, account_id, "本机测试账号"),
            ],
        )

    def test_shared_cookie_is_not_deleted_from_another_active_account(self):
        first_id = self._insert_account("shared.json")
        second_id = self._insert_account("shared.json")
        shared_cookie = self.cookies_directory / "shared.json"
        shared_cookie.write_text("shared-private-state", encoding="utf-8")

        result = disconnect_media_account(
            self.database_path,
            first_id,
            cookies_directory=self.cookies_directory,
        )

        self.assertTrue(result["cookie_shared"])
        self.assertFalse(result["cookie_removed"])
        self.assertEqual(
            shared_cookie.read_text(encoding="utf-8"),
            "shared-private-state",
        )
        cleanup = cleanup_disconnected_account_files(
            self.database_path,
            self.cookies_directory,
        )
        self.assertEqual(cleanup, {"removed": 0, "remaining": 0})
        self.assertTrue(shared_cookie.exists())
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT id, status, filePath FROM user_info ORDER BY id"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (first_id, DISCONNECTED_ACCOUNT_STATUS, ""),
                (second_id, 1, "shared.json"),
            ],
        )

    def test_processing_publish_prevents_credential_removal(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text("private", encoding="utf-8")
        self._insert_job(account_id, status="processing")

        with self.assertRaises(MediaAccountBusyError):
            disconnect_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
            )

        self.assertTrue(cookie_file.exists())

    async def test_check_disconnect_race_finishes_with_domain_error_not_type_error(self):
        account_id = self._insert_account()
        cookie_file = self.cookies_directory / "account.json"
        cookie_file.write_text("private", encoding="utf-8")

        def disconnect_during_check(_account_type, _filename):
            disconnect_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
            )
            return True

        with self.assertRaises(MediaAccountNotFoundError):
            await check_media_account(
                self.database_path,
                account_id,
                cookies_directory=self.cookies_directory,
                checker=disconnect_during_check,
            )


class MediaAccountDisconnectApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "geo.db"
        self.cookies_directory = self.root / "cookies"
        self.cookies_directory.mkdir()
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.account_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (9, 'account.json', '接口测试账号', 1)
                """
            ).lastrowid
            connection.commit()
        (self.cookies_directory / "account.json").write_text(
            "{}", encoding="utf-8"
        )
        self.original_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE_PATH",
                "COOKIES_DIRECTORY",
                "TESTING",
                "ACCESS_CONTROL_ENABLED",
            )
        }
        app.config.update(
            DATABASE_PATH=self.database_path,
            COOKIES_DIRECTORY=self.cookies_directory,
            TESTING=True,
            ACCESS_CONTROL_ENABLED=False,
        )
        self.client = app.test_client()
        with active_queues_lock:
            active_queues.clear()
            active_account_checks.clear()

    def tearDown(self):
        with active_queues_lock:
            active_queues.clear()
            active_account_checks.clear()
        app.config.update(self.original_config)
        self.temporary_directory.cleanup()

    def test_requires_explicit_confirmation(self):
        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect", json={}
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue((self.cookies_directory / "account.json").exists())

    def test_requires_authenticated_operator_when_access_control_is_enabled(self):
        app.config["ACCESS_CONTROL_ENABLED"] = True

        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect",
            json={"confirm_unbind": True},
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue((self.cookies_directory / "account.json").exists())

    def test_disconnect_contract_removes_local_binding_without_platform_call(self):
        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect",
            json={"confirm_unbind": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["account_id"], self.account_id)
        self.assertFalse((self.cookies_directory / "account.json").exists())
        overview = self.client.get("/api/media-accounts").get_json()["data"]
        self.assertEqual(overview["accounts"], [])
        with closing(sqlite3.connect(self.database_path)) as connection:
            audit_row = connection.execute(
                "SELECT status, userName FROM user_info WHERE id = ?",
                (self.account_id,),
            ).fetchone()
        self.assertEqual(
            audit_row,
            (DISCONNECTED_ACCOUNT_STATUS, "接口测试账号"),
        )

    def test_disconnect_payload_is_not_subject_to_visibility_sample_count_rules(self):
        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect",
            json={"confirm_unbind": True, "sample_count": 999},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["account_id"], self.account_id)

    def test_cookie_unlink_failure_returns_accepted_warning_then_refresh_cleans(self):
        with patch.object(Path, "unlink", side_effect=PermissionError("in use")):
            response = self.client.post(
                f"/api/media-accounts/{self.account_id}/disconnect",
                json={"confirm_unbind": True},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["data"]["cleanup_pending"])
        self.assertIn("刷新页面", response.get_json()["msg"])
        self.assertTrue((self.cookies_directory / "account.json").exists())

        refreshed = self.client.get("/api/media-accounts")

        self.assertEqual(refreshed.status_code, 200)
        self.assertFalse((self.cookies_directory / "account.json").exists())

    def test_account_check_blocks_disconnect_without_race(self):
        with active_queues_lock:
            active_account_checks.add(self.account_id)

        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect",
            json={"confirm_unbind": True},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("正在检测", response.get_json()["msg"])
        self.assertTrue((self.cookies_directory / "account.json").exists())

    def test_active_login_must_be_ended_before_saved_account_disconnect(self):
        session_key = ("9", "接口测试账号")
        with active_queues_lock:
            active_queues[session_key] = LoginStatusQueue()

        response = self.client.post(
            f"/api/media-accounts/{self.account_id}/disconnect",
            json={"confirm_unbind": True},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("结束本次登录", response.get_json()["msg"])
        self.assertTrue((self.cookies_directory / "account.json").exists())

    def test_legacy_destructive_get_is_disabled(self):
        response = self.client.get(
            "/deleteAccount", query_string={"id": self.account_id}
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue((self.cookies_directory / "account.json").exists())


if __name__ == "__main__":
    unittest.main()
