import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountNotFoundError,
)


class MediaAccountsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "api.db"
        initialize_database(self.db_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = self.db_path
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        self.temp_dir.cleanup()

    def test_returns_structured_empty_platform_overview(self):
        response = self.client.get("/api/media-accounts")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["summary"]["total"], 0)
        self.assertEqual(len(data["platforms"]), 9)
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


if __name__ == "__main__":
    unittest.main()
