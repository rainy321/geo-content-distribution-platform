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


if __name__ == "__main__":
    unittest.main()
