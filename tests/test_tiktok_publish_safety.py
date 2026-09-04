import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from uploader.tk_uploader.main_chrome import TIKTOK_UPLOAD_URL, TiktokVideo


class TiktokPublishSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _publisher(self, account_file: Path, **overrides):
        return TiktokVideo(
            title="GEO 内容生成与分发链路测试说明",
            file_path="test.mp4",
            tags=["GEO"],
            publish_date=0,
            account_file=account_file,
            **overrides,
        )

    def test_dry_run_forces_visible_preview_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(
                Path(temp_dir) / "account.json",
                dry_run=True,
                headless=False,
                preview_seconds=15,
            )
        self.assertTrue(publisher.dry_run)
        self.assertFalse(publisher.headless)
        self.assertEqual(publisher.preview_seconds, 15)

    def test_upload_url_requests_english_without_homepage_navigation(self):
        self.assertEqual(
            TIKTOK_UPLOAD_URL,
            "https://www.tiktok.com/tiktokstudio/upload?lang=en",
        )

    async def test_unknown_result_never_clicks_post_twice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            button = MagicMock()
            button.wait_for = AsyncMock()
            button.click = AsyncMock()
            publisher.locator_base = MagicMock()
            publisher.locator_base.locator.return_value.nth.return_value = button
            page = MagicMock()
            page.wait_for_url = AsyncMock(side_effect=TimeoutError("unknown"))

            with self.assertRaisesRegex(RuntimeError, "automatic retry was disabled"):
                await publisher.click_publish(page)

        button.click.assert_awaited_once()

    async def test_default_browser_path_does_not_launch_dot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            publisher.local_executable_path = Path(".")
            playwright = MagicMock()
            playwright.chromium.launch = AsyncMock(
                side_effect=RuntimeError("stop after launch configuration")
            )

            with self.assertRaisesRegex(RuntimeError, "stop after launch configuration"):
                await publisher.upload(playwright)

        playwright.chromium.launch.assert_awaited_once_with(
            headless=publisher.headless
        )


if __name__ == "__main__":
    unittest.main()
