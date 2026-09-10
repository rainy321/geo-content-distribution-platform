import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.tencent_uploader.main import (
    TENCENT_UPLOAD_URL,
    TencentVideo,
    _is_tencent_login_url,
    _wait_for_authenticated_upload_page,
)


class TencentPublishSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _publisher(self, account_file: Path) -> TencentVideo:
        return TencentVideo(
            title="GEO 内容生成与分发链路测试说明",
            file_path="test.mp4",
            tags=["GEO"],
            publish_date=0,
            account_file=account_file,
            dry_run=True,
        )

    async def test_upload_timeout_stops_before_publish_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            publish_buttons = MagicMock()
            publish_buttons.count = AsyncMock(return_value=1)
            publish_button = MagicMock()
            publish_button.is_visible = AsyncMock(return_value=True)
            publish_button.get_attribute = AsyncMock(
                return_value="weui-desktop-btn_disabled"
            )
            publish_button.is_disabled = AsyncMock(return_value=True)
            publish_buttons.nth.return_value = publish_button
            page.get_by_role.return_value = publish_buttons
            status_locator = MagicMock()
            status_locator.count = AsyncMock(return_value=0)
            status_locator.all_inner_texts = AsyncMock(
                return_value=["上传中\n0%\n取消上传"]
            )
            page.locator.return_value = status_locator
            page.screenshot = AsyncMock()

            with patch(
                "uploader.tencent_uploader.main.asyncio.sleep",
                new=AsyncMock(),
            ):
                with self.assertRaisesRegex(RuntimeError, "visible_progress 0%"):
                    await publisher.wait_for_upload_complete(
                        page,
                        max_retries=1,
                    )

        page.screenshot.assert_awaited_once()

    async def test_visible_enabled_publish_button_marks_upload_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            publish_buttons = MagicMock()
            publish_buttons.count = AsyncMock(return_value=1)
            publish_button = MagicMock()
            publish_button.is_visible = AsyncMock(return_value=True)
            publish_button.get_attribute = AsyncMock(return_value="primary")
            publish_button.is_disabled = AsyncMock(return_value=False)
            publish_buttons.nth.return_value = publish_button
            page.get_by_role.return_value = publish_buttons

            await publisher.wait_for_upload_complete(page, max_retries=1)

        publish_button.is_disabled.assert_awaited_once()

    async def test_completed_upload_transport_unblocks_after_two_stable_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            publisher._upload_transport_completed = True
            page = MagicMock()
            absent = MagicMock()
            absent.first = absent
            absent.count = AsyncMock(return_value=0)
            absent.is_visible = AsyncMock(return_value=False)
            absent.all_inner_texts = AsyncMock(return_value=[])
            page.get_by_role.return_value = absent
            page.locator.return_value = absent
            page.screenshot = AsyncMock()

            with patch(
                "uploader.tencent_uploader.main.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep:
                await publisher.wait_for_upload_complete(page, max_retries=2)

        sleep.assert_awaited_once_with(2)

    async def test_short_title_is_filled_before_upload_ready_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            order = []
            page = MagicMock()
            publisher.open_upload_page = AsyncMock()
            publisher.upload_video_file = AsyncMock()
            publisher.prepare_video_for_publish = AsyncMock()
            publisher.set_short_title = AsyncMock(
                side_effect=lambda *_args: order.append("short_title")
            )
            publisher.wait_for_upload_complete = AsyncMock(
                side_effect=lambda *_args: order.append("upload_ready")
            )
            publisher.apply_original_statement = AsyncMock()
            publisher.set_thumbnail = AsyncMock()
            publisher.validate_upload_args = AsyncMock()
            page.screenshot = AsyncMock()
            context = MagicMock()
            context.new_page = AsyncMock(return_value=page)
            context.storage_state = AsyncMock()
            context.close = AsyncMock()
            browser = MagicMock()
            browser.new_context = AsyncMock(return_value=context)
            browser.close = AsyncMock()
            playwright = MagicMock()
            playwright.chromium.launch = AsyncMock(return_value=browser)
            publisher.preview_seconds = 0

            await publisher.upload(playwright)

        self.assertEqual(order, ["short_title", "upload_ready"])

    async def test_upload_file_selection_is_verified_and_network_listener_is_installed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            account_file = temp_path / "account.json"
            video_file = temp_path / "test.mp4"
            video_file.write_bytes(b"video-content")
            publisher = self._publisher(account_file)
            page = MagicMock()
            file_inputs = MagicMock()
            file_input = MagicMock()
            file_inputs.first = file_input
            page.locator.return_value = file_inputs
            file_input.wait_for = AsyncMock()
            file_input.set_input_files = AsyncMock()
            file_input.evaluate = AsyncMock(
                return_value=[
                    {
                        "name": "test.mp4",
                        "size": video_file.stat().st_size,
                        "type": "video/mp4",
                    }
                ]
            )

            await publisher.upload_video_file(page, str(video_file))

        file_input.set_input_files.assert_awaited_once_with(str(video_file))
        self.assertEqual(page.on.call_count, 2)
        self.assertTrue(
            any(
                "file_selected test.mp4" in item
                for item in publisher._upload_diagnostics
            )
        )

    def test_final_multipart_response_marks_transport_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            listeners = {}
            page = MagicMock()
            page.on.side_effect = lambda event, callback: listeners.__setitem__(
                event,
                callback,
            )
            publisher._install_upload_network_diagnostics(page)
            request = MagicMock()
            request.method = "POST"
            request.resource_type = "xhr"
            response = MagicMock()
            response.request = request
            response.status = 200
            response.url = (
                "https://finderassistancea.video.qq.com/completepartuploaddfs"
                "?upload_token=secret"
            )

            listeners["response"](response)

        self.assertTrue(publisher._upload_transport_completed)
        self.assertTrue(
            any(
                item.startswith("upload_transport_complete POST 200")
                for item in publisher._upload_diagnostics
            )
        )
        self.assertTrue(
            all("upload_token" not in item for item in publisher._upload_diagnostics)
        )

    async def test_page_may_clear_file_input_after_accepting_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_file = temp_path / "test.mp4"
            video_file.write_bytes(b"video-content")
            publisher = self._publisher(temp_path / "account.json")
            page = MagicMock()
            file_inputs = MagicMock()
            file_input = MagicMock()
            file_inputs.first = file_input
            page.locator.return_value = file_inputs
            file_input.wait_for = AsyncMock()
            file_input.set_input_files = AsyncMock()
            file_input.evaluate = AsyncMock(return_value=[])

            await publisher.upload_video_file(page, str(video_file))

        self.assertTrue(
            any(
                "file_input_cleared_after_selection" in item
                for item in publisher._upload_diagnostics
            )
        )

    def test_request_endpoint_strips_query_and_fragment(self):
        endpoint = TencentVideo._safe_request_endpoint(
            "https://upload.example.test/media/chunk?token=secret#part"
        )

        self.assertEqual(endpoint, "https://upload.example.test/media/chunk")

    def test_login_url_is_never_treated_as_authenticated(self):
        self.assertTrue(
            _is_tencent_login_url("https://channels.weixin.qq.com/login.html")
        )
        self.assertFalse(_is_tencent_login_url(TENCENT_UPLOAD_URL))

    async def test_upload_file_input_proves_authenticated_editor_loaded(self):
        page = MagicMock()
        page.url = TENCENT_UPLOAD_URL
        absent_marker = MagicMock()
        absent_marker.count = AsyncMock(return_value=0)
        absent_marker.is_visible = AsyncMock(return_value=False)
        text_locator = MagicMock()
        text_locator.first = absent_marker
        page.get_by_text.return_value = text_locator
        role_locator = MagicMock()
        role_locator.first = absent_marker
        page.get_by_role.return_value = role_locator
        file_input = MagicMock()
        file_input.count = AsyncMock(return_value=1)

        def locate(selector):
            if selector == 'input[type="file"]':
                return file_input
            locator = MagicMock()
            locator.first = absent_marker
            return locator

        page.locator.side_effect = locate

        authenticated = await _wait_for_authenticated_upload_page(
            page,
            max_checks=1,
            poll_interval=0,
        )

        self.assertTrue(authenticated)

    async def test_missing_original_entry_keeps_platform_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            page = MagicMock()
            absent = MagicMock()
            absent.first = absent
            absent.count = AsyncMock(return_value=0)
            absent.is_visible = AsyncMock(return_value=False)
            page.get_by_label.return_value = absent
            page.locator.return_value = absent
            page.screenshot = AsyncMock()

            await publisher.apply_original_statement(page)

        page.screenshot.assert_awaited_once()

    async def test_required_original_entry_still_blocks_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = self._publisher(Path(temp_dir) / "account.json")
            publisher.require_original_statement = True
            page = MagicMock()
            absent = MagicMock()
            absent.first = absent
            absent.count = AsyncMock(return_value=0)
            absent.is_visible = AsyncMock(return_value=False)
            page.get_by_label.return_value = absent
            page.locator.return_value = absent
            page.screenshot = AsyncMock()

            with self.assertRaisesRegex(RuntimeError, "任务要求必须声明原创"):
                await publisher.apply_original_statement(page)


if __name__ == "__main__":
    unittest.main()
