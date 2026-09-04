import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

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
            button.get_attribute = AsyncMock(return_value=None)
            button.click = AsyncMock()
            publisher.locator_base = MagicMock()
            publisher.locator_base.locator.return_value.first = button
            page = MagicMock()
            page.url = TIKTOK_UPLOAD_URL
            page.wait_for_url = AsyncMock(side_effect=TimeoutError("unknown"))
            page.wait_for_timeout = AsyncMock()
            page.screenshot = AsyncMock()
            empty_dialog = MagicMock()
            empty_dialog.count = AsyncMock(return_value=0)
            dialog_result = MagicMock()
            dialog_result.first = empty_dialog
            diagnostics = MagicMock()
            diagnostics.all_inner_texts = AsyncMock(return_value=[])
            page.locator.side_effect = [*[dialog_result] * 20, diagnostics]

            with self.assertRaisesRegex(RuntimeError, "automatic retry was disabled"):
                await publisher.click_publish(page)

        button.click.assert_awaited_once()

    async def test_known_copyright_prompt_is_confirmed_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            post_button = MagicMock()
            post_button.wait_for = AsyncMock()
            post_button.get_attribute = AsyncMock(return_value=None)
            post_button.click = AsyncMock()
            publisher.locator_base = MagicMock()
            publisher.locator_base.locator.return_value.first = post_button

            page = MagicMock()
            page.url = TIKTOK_UPLOAD_URL
            page.screenshot = AsyncMock()
            page.wait_for_url = AsyncMock()
            dialog = MagicMock()
            dialog.count = AsyncMock(return_value=1)
            dialog.is_visible = AsyncMock(return_value=True)
            dialog.inner_text = AsyncMock(
                return_value="继续发布？\n版权检查未完成。\n取消\n立即发布"
            )
            confirm_button = MagicMock()
            confirm_button.count = AsyncMock(return_value=1)
            confirm_button.is_visible = AsyncMock(return_value=True)
            confirm_button.click = AsyncMock()
            confirm_result = MagicMock()
            confirm_result.first = confirm_button
            dialog.get_by_role.return_value = confirm_result
            dialog_result = MagicMock()
            dialog_result.first = dialog
            page.locator.return_value = dialog_result

            await publisher.click_publish(page)

        post_button.click.assert_awaited_once()
        confirm_button.click.assert_awaited_once()
        page.wait_for_url.assert_awaited_once()

    async def test_late_joyride_overlay_retries_editor_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            publisher.tags = []
            editor = MagicMock()
            editor.wait_for = AsyncMock()
            editor.click = AsyncMock(
                side_effect=[
                    PlaywrightTimeoutError("overlay"),
                    None,
                ]
            )
            publisher.locator_base = MagicMock()
            publisher.locator_base.locator.return_value = editor
            publisher.dismiss_joyride_overlay = AsyncMock(return_value=True)
            page = MagicMock()
            page.keyboard.press = AsyncMock()
            page.keyboard.insert_text = AsyncMock()
            page.wait_for_timeout = AsyncMock()

            await publisher.add_title_tags(page)

        self.assertEqual(editor.click.await_count, 2)
        editor.wait_for.assert_awaited_once_with(state="visible", timeout=60000)
        self.assertEqual(publisher.dismiss_joyride_overlay.await_count, 2)
        publisher.dismiss_joyride_overlay.assert_awaited_with(page)

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

    async def test_joyride_overlay_is_dismissed_before_editing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            portal = MagicMock()
            active_tour = MagicMock()
            dismiss_button = MagicMock()
            portal_result = MagicMock()
            active_result = MagicMock()
            dismiss_result = MagicMock()

            page.locator.side_effect = [portal_result, active_result]
            portal_result.first = portal
            active_result.first = active_tour
            portal.count = AsyncMock(return_value=1)
            active_tour.count = AsyncMock(side_effect=[1, 1, 0])
            active_tour.is_visible = AsyncMock(return_value=True)
            portal.locator.return_value = dismiss_result
            dismiss_result.first = dismiss_button
            dismiss_button.count = AsyncMock(return_value=1)
            dismiss_button.is_visible = AsyncMock(return_value=True)
            dismiss_button.click = AsyncMock()
            page.wait_for_timeout = AsyncMock()

            dismissed = await publisher.dismiss_joyride_overlay(page)

        self.assertTrue(dismissed)
        dismiss_button.click.assert_awaited_once_with(force=True)
        self.assertEqual(page.locator.call_count, 2)

    async def test_absent_joyride_overlay_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            portal = MagicMock()
            portal_result = MagicMock()
            active_result = MagicMock()
            active_tour = MagicMock()

            page.locator.side_effect = [portal_result, active_result]
            portal_result.first = portal
            active_result.first = active_tour
            portal.count = AsyncMock(return_value=0)

            dismissed = await publisher.dismiss_joyride_overlay(page)

        self.assertFalse(dismissed)
        active_tour.count.assert_not_called()

    async def test_exact_title_public_link_is_returned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            link = MagicMock()
            link.wait_for = AsyncMock()
            link.get_attribute = AsyncMock(
                return_value="/@creator/video/7681719912934477072"
            )
            filtered = MagicMock()
            filtered.first = link
            page.locator.return_value.filter.return_value = filtered

            public_url = await publisher.get_published_video_url(page)

        self.assertEqual(
            public_url,
            "https://www.tiktok.com/@creator/video/7681719912934477072",
        )
        page.locator.return_value.filter.assert_called_once_with(
            has_text="GEO 内容生成与分发链路测试说明"
        )


if __name__ == "__main__":
    unittest.main()
