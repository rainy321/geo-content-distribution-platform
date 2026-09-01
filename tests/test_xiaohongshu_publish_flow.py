import unittest
from unittest.mock import AsyncMock, Mock

from services.xiaohongshu_publish_flow import click_exact_publish_and_observe


class _Candidates:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class XiaohongshuPublishFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _button():
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()
        return button

    async def test_requires_one_exact_publish_button(self):
        page = Mock()
        page.get_by_role.return_value = _Candidates([])
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "页面结构可能变化"):
            await click_exact_publish_and_observe(page)

    async def test_internal_success_page_is_processing_not_public_success(self):
        button = self._button()
        page = Mock()
        page.url = "https://creator.xiaohongshu.com/publish/success?source=web"
        page.get_by_role.return_value = _Candidates([button])
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "processing")
        self.assertFalse(result["success"])
        button.click.assert_awaited_once()

    async def test_public_note_navigation_is_success(self):
        button = self._button()
        page = Mock()
        page.url = "https://www.xiaohongshu.com/explore/66d123abc456def789012345"
        page.get_by_role.return_value = _Candidates([button])
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["url"], page.url)


if __name__ == "__main__":
    unittest.main()
