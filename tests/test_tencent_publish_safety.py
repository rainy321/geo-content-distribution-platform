import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.tencent_uploader.main import TencentVideo


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
            page.locator.return_value = status_locator
            page.screenshot = AsyncMock()

            with patch(
                "uploader.tencent_uploader.main.asyncio.sleep",
                new=AsyncMock(),
            ):
                with self.assertRaisesRegex(RuntimeError, "上传等待超时"):
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


if __name__ == "__main__":
    unittest.main()
