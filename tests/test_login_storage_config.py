import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import ANY, AsyncMock, patch

from db.createTable import initialize_database
from myUtils.auth import check_cookie
from myUtils.login import xiaohongshu_cookie_gen
from sau_backend import app, run_async_function


class LoginStorageConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "configured.db"
        self.cookies_directory = self.temp_root / "configured-cookies"
        self.cookies_directory.mkdir()
        initialize_database(self.db_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_xiaohongshu_login_persists_to_injected_storage(self):
        async def fake_login(account_file, *, headless):
            Path(account_file).write_text("{}", encoding="utf-8")
            return {"success": True, "message": "ok"}

        status_queue = Queue()
        with patch(
            "uploader.xiaohongshu_uploader.main.xiaohongshu_cookie_gen",
            new=AsyncMock(side_effect=fake_login),
        ):
            result = await xiaohongshu_cookie_gen(
                "xhs-isolated",
                status_queue,
                database_path=self.db_path,
                cookies_directory=self.cookies_directory,
            )

        self.assertTrue(result)
        self.assertEqual(status_queue.get_nowait(), "MANUAL_LOGIN")
        self.assertEqual(status_queue.get_nowait(), "200")
        with sqlite3.connect(self.db_path) as conn:
            account = conn.execute(
                "SELECT type, filePath, userName, status FROM user_info"
            ).fetchone()
        self.assertEqual(account[0], 1)
        self.assertEqual(account[2:], ("xhs-isolated", 1))
        self.assertTrue((self.cookies_directory / account[1]).is_file())

    async def test_cookie_check_uses_injected_directory(self):
        expected_path = self.cookies_directory / "account.json"
        expected_path.write_text("{}", encoding="utf-8")
        with patch(
            "myUtils.auth.cookie_auth_zhihu",
            new=AsyncMock(return_value=True),
        ) as checker:
            connected = await check_cookie(
                9,
                "account.json",
                cookies_directory=self.cookies_directory,
            )

        self.assertTrue(connected)
        checker.assert_awaited_once_with(expected_path)


class LoginThreadStorageConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "thread.db"
        self.cookies_directory = self.temp_root / "thread-cookies"
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_cookies_directory = app.config["COOKIES_DIRECTORY"]
        app.config.update(
            DATABASE_PATH=self.db_path,
            COOKIES_DIRECTORY=self.cookies_directory,
        )

    def tearDown(self):
        app.config.update(
            DATABASE_PATH=self.original_database_path,
            COOKIES_DIRECTORY=self.original_cookies_directory,
        )
        self.temp_dir.cleanup()

    def test_web_login_thread_forwards_runtime_storage(self):
        with patch(
            "myUtils.login.xiaohongshu_cookie_gen",
            new=AsyncMock(return_value=True),
        ) as login_runner:
            run_async_function("1", "account", Queue())

        login_runner.assert_awaited_once_with(
            "account",
            ANY,
            database_path=self.db_path,
            cookies_directory=self.cookies_directory,
        )

    def test_web_login_thread_dispatches_tiktok(self):
        with patch(
            "myUtils.login.tiktok_cookie_gen",
            new=AsyncMock(return_value=True),
        ) as login_runner:
            run_async_function("10", "tiktok-account", Queue())

        login_runner.assert_awaited_once_with(
            "tiktok-account",
            ANY,
            database_path=self.db_path,
            cookies_directory=self.cookies_directory,
        )


class LegacyStorageConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "legacy.db"
        self.cookies_directory = self.temp_root / "cookies"
        self.media_root = self.temp_root / "media"
        self.cookies_directory.mkdir()
        self.media_root.mkdir()
        initialize_database(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (9, "account.json", "isolated-account", 1),
            )
            self.account_id = cursor.lastrowid

        self.original_config = {
            "DATABASE_PATH": app.config["DATABASE_PATH"],
            "COOKIES_DIRECTORY": app.config["COOKIES_DIRECTORY"],
            "MEDIA_ROOT": app.config["MEDIA_ROOT"],
            "TESTING": app.config["TESTING"],
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            COOKIES_DIRECTORY=self.cookies_directory,
            MEDIA_ROOT=self.media_root,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def test_legacy_account_routes_use_runtime_database_and_cookie_root(self):
        listed = self.client.get("/getAccounts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["data"][0][3], "isolated-account")

        updated = self.client.post(
            "/updateUserinfo",
            json={"id": self.account_id, "type": 9, "userName": "renamed"},
        )
        self.assertEqual(updated.status_code, 200)

        uploaded = self.client.post(
            "/uploadCookie",
            data={
                "id": str(self.account_id),
                "platform": "zhihu",
                "file": (io.BytesIO(b'{"cookies": []}'), "account.json"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 200)
        cookie_path = self.cookies_directory / "account.json"
        self.assertEqual(cookie_path.read_bytes(), b'{"cookies": []}')

        downloaded = self.client.get(
            "/downloadCookie", query_string={"filePath": "account.json"}
        )
        try:
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.data, b'{"cookies": []}')
        finally:
            downloaded.close()

        deleted = self.client.get(
            "/deleteAccount", query_string={"id": self.account_id}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(cookie_path.exists())
        with sqlite3.connect(self.db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM user_info WHERE id = ?",
                (self.account_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_legacy_cookie_check_receives_runtime_cookie_root(self):
        with patch(
            "myUtils.auth.check_cookie",
            new=AsyncMock(return_value=True),
        ) as checker:
            response = self.client.get("/getValidAccounts")

        self.assertEqual(response.status_code, 200)
        checker.assert_awaited_once_with(
            9,
            "account.json",
            cookies_directory=self.cookies_directory,
        )

    def test_legacy_upload_uses_runtime_media_root_and_rejects_paths(self):
        uploaded = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(b"video"), "sample.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 200)
        stored_filename = uploaded.get_json()["data"]
        self.assertEqual((self.media_root / stored_filename).read_bytes(), b"video")

        rejected = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(b"escape"), "../escape.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()
