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
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "api.db"
        self.cookies_directory = self.temp_root / "cookies"
        self.cookies_directory.mkdir()
        initialize_database(self.db_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_cookies_directory = app.config["COOKIES_DIRECTORY"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.config["COOKIES_DIRECTORY"] = self.cookies_directory
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.config["COOKIES_DIRECTORY"] = self.original_cookies_directory
        app.config["TESTING"] = self.original_testing
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

    def test_readonly_overview_forwards_normalized_schedule_reference(self):
        with patch('sau_backend.get_media_accounts_overview', return_value={'accounts':[]}) as mocked:
            response = self.client.get('/api/media-accounts', query_string={'publish_at':'2026-10-25T09:30:00+08:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_args.kwargs['publish_at'], '2026-10-25T09:30:00+08:00')

    def test_invalid_reference_is_rejected_before_any_account_or_browser_work(self):
        for value in ('', '2026-10-25', 'not-a-time', '2026-13-45T10:00:00', 'x'*81):
            with patch('sau_backend.get_media_accounts_overview') as mocked:
                response = self.client.get('/api/media-accounts', query_string={'publish_at':value})
                self.assertEqual(response.status_code,400)
                mocked.assert_not_called()
        with patch('sau_backend.get_media_accounts_overview') as mocked:
            response=self.client.get('/api/media-accounts?publish_at=2026-10-25T10:00:00&publish_at=2026-10-26T10:00:00')
            self.assertEqual(response.status_code,400)
            mocked.assert_not_called()

    def test_overview_execution_mode_is_server_owned(self):
        with patch.dict(app.config, {'DEMO_MODE':True,'ALLOW_REAL_PUBLISHING':False}):
            with patch('sau_backend.get_media_accounts_overview', return_value={'accounts':[]}):
                data=self.client.get('/api/media-accounts?execution_mode=real').get_json()['data']
        self.assertEqual(data['execution_mode'],'demo')
        self.assertFalse(data['real_publishing_enabled'])
        with patch.dict(app.config, {'DEMO_MODE':False,'ALLOW_REAL_PUBLISHING':True}):
            with patch('sau_backend.get_media_accounts_overview', return_value={'accounts':[]}):
                data=self.client.get('/api/media-accounts?execution_mode=demo').get_json()['data']
        self.assertEqual(data['execution_mode'],'real')
        self.assertTrue(data['real_publishing_enabled'])

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
