import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
    MediaAccountRuntimeDisabledError,
)


class MediaAccountsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "api.db"
        self.cookies_directory = self.temp_root / "cookies"
        self.cookies_directory.mkdir()
        initialize_database(self.db_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_cookies_directory = app.config["COOKIES_DIRECTORY"]
        self.original_testing = app.testing
        self.original_bilibili_runtime = app.config["ENABLE_BILIBILI_RUNTIME"]
        app.config["DATABASE_PATH"] = self.db_path
        app.config["COOKIES_DIRECTORY"] = self.cookies_directory
        app.config["TESTING"] = True
        app.config["ENABLE_BILIBILI_RUNTIME"] = False
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.config["COOKIES_DIRECTORY"] = self.original_cookies_directory
        app.config["TESTING"] = self.original_testing
        app.config["ENABLE_BILIBILI_RUNTIME"] = self.original_bilibili_runtime
        self.temp_dir.cleanup()

    def test_returns_structured_empty_platform_overview(self):
        response = self.client.get("/api/media-accounts")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["summary"]["total"], 0)
        self.assertEqual(len(data["platforms"]), 10)
        self.assertEqual(data["platforms"][0]["key"], "zhihu")

    def test_check_returns_service_result(self):
        account = {
            "id": 7,
            "platform_key": "zhihu",
            "account_name": "主账号",
            "status": "connected",
        }
        with patch(
            "sau_backend.check_media_account",
            new=AsyncMock(return_value=account),
        ) as mocked:
            response = self.client.post("/api/media-accounts/7/check")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], account)
        self.assertEqual(mocked.await_count, 1)

    def test_check_maps_not_found_and_platform_failure(self):
        with patch(
            "sau_backend.check_media_account",
            new=AsyncMock(side_effect=MediaAccountNotFoundError("媒体账号不存在")),
        ):
            missing = self.client.post("/api/media-accounts/999/check")
        with patch(
            "sau_backend.check_media_account",
            new=AsyncMock(side_effect=MediaAccountCheckError("平台检测失败")),
        ):
            failed = self.client.post("/api/media-accounts/1/check")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(failed.status_code, 502)

    def test_check_maps_disabled_bilibili_runtime_without_calling_platform(self):
        with patch(
            "sau_backend.check_media_account",
            new=AsyncMock(
                side_effect=MediaAccountRuntimeDisabledError(
                    "ENABLE_BILIBILI_RUNTIME=true"
                )
            ),
        ):
            response = self.client.post("/api/media-accounts/6/check")

        self.assertEqual(response.status_code, 403)
        self.assertIn("ENABLE_BILIBILI_RUNTIME", response.get_json()["msg"])


if __name__ == "__main__":
    unittest.main()
