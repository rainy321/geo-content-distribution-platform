import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.media_account_service import (
    MediaAccountCheckError,
    MediaAccountFrozenError,
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
        self.original_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE_PATH",
                "COOKIES_DIRECTORY",
                "TESTING",
                "ENABLE_BILIBILI_RUNTIME",
                "ALLOW_REAL_PUBLISHING",
                "REAL_PUBLISHING_AUTHORIZED",
                "PUBLISHING_RUNTIME_READY",
            )
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            COOKIES_DIRECTORY=self.cookies_directory,
            TESTING=True,
            ENABLE_BILIBILI_RUNTIME=False,
            ALLOW_REAL_PUBLISHING=True,
            REAL_PUBLISHING_AUTHORIZED=True,
            PUBLISHING_RUNTIME_READY=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def test_returns_structured_empty_platform_overview(self):
        response = self.client.get("/api/media-accounts")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["summary"]["total"], 0)
        self.assertEqual(len(data["platforms"]), 11)
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

    def test_check_returns_structured_risk_when_account_is_frozen(self):
        risk_event = {
            "category": "permanent_ban",
            "label": "账号永久封禁",
            "recommended_action": "前往平台安全中心申诉",
            "frozen": True,
        }
        with patch(
            "sau_backend.check_media_account",
            new=AsyncMock(
                side_effect=MediaAccountFrozenError(
                    "账号永久封禁",
                    risk_event=risk_event,
                )
            ),
        ):
            response = self.client.post("/api/media-accounts/7/check")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["data"]["risk_event"]["category"],
            "permanent_ban",
        )

    def test_risk_resolution_requires_confirmation_and_keeps_login_required(self):
        missing_confirmation = self.client.post(
            "/api/media-accounts/7/risk/resolve",
            json={"category": "verification_required"},
        )
        self.assertEqual(missing_confirmation.status_code, 400)

        service_result = {
            "account_id": 7,
            "resolved_count": 1,
            "requires_login": True,
            "account_enabled": False,
        }
        with patch(
            "sau_backend.resolve_media_account_risks",
            return_value=service_result,
        ) as mocked:
            response = self.client.post(
                "/api/media-accounts/7/risk/resolve",
                json={
                    "confirm_recovery": True,
                    "category": "verification_required",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["data"]["requires_login"])
        self.assertFalse(response.get_json()["data"]["account_enabled"])
        mocked.assert_called_once_with(
            self.db_path,
            7,
            category="verification_required",
        )

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

    def test_safe_build_gate_precedes_playwright_import(self):
        app.config["REAL_PUBLISHING_AUTHORIZED"] = False
        app.config["ALLOW_REAL_PUBLISHING"] = False

        with patch.dict(sys.modules, {"myUtils.auth": None}), patch(
            "sau_backend.check_media_account",
            new=AsyncMock(),
        ) as mocked:
            response = self.client.post("/api/media-accounts/7/check")

        self.assertEqual(response.status_code, 403)
        mocked.assert_not_awaited()

    def test_missing_runtime_gate_precedes_playwright_import(self):
        app.config["REAL_PUBLISHING_AUTHORIZED"] = True
        app.config["PUBLISHING_RUNTIME_READY"] = False
        app.config["ALLOW_REAL_PUBLISHING"] = False

        with patch.dict(sys.modules, {"myUtils.auth": None}), patch(
            "sau_backend.check_media_account",
            new=AsyncMock(),
        ) as mocked:
            response = self.client.post("/api/media-accounts/7/check")

        self.assertEqual(response.status_code, 503)
        mocked.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
