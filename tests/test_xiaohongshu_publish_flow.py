import unittest
from unittest.mock import AsyncMock, Mock, patch

from services.xiaohongshu_publish_flow import (
    _find_published_note_id,
    _reconcile_posted_note,
    click_exact_publish_and_observe,
)


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

    async def test_internal_success_page_reconciles_exact_published_note(self):
        button = self._button()
        page = Mock()
        page.url = "https://creator.xiaohongshu.com/publish/success?source=web"
        page.get_by_role.return_value = _Candidates([button])
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()
        expected = {
            "status": "success",
            "success": True,
            "url": "https://www.xiaohongshu.com/explore/6a977888000000001103bb96",
        }

        with patch(
            "services.xiaohongshu_publish_flow._reconcile_posted_note",
            new=AsyncMock(return_value=expected),
        ) as reconcile:
            result = await click_exact_publish_and_observe(
                page,
                title="GEO 内容生成与分发链路测试说明",
            )

        self.assertEqual(result, expected)
        reconcile.assert_awaited_once_with(
            page,
            "GEO 内容生成与分发链路测试说明",
        )
        button.click.assert_awaited_once()

    def test_finds_exact_note_id_in_nested_posted_payload(self):
        payload = {
            "data": {
                "notes": [
                    {"display_title": "其他标题", "id": "111111111111111111111111"},
                    {
                        "display_title": "GEO 内容生成与分发链路测试说明",
                        "id": "6A977888000000001103BB96",
                    },
                ]
            }
        }

        note_id = _find_published_note_id(
            payload,
            "GEO 内容生成与分发链路测试说明",
        )

        self.assertEqual(note_id, "6a977888000000001103bb96")

    async def test_reconciles_posted_endpoint_without_second_publish(self):
        posted_response = Mock()
        posted_response.url = (
            "https://creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted"
        )
        posted_response.status = 200
        posted_response.json = AsyncMock(
            return_value={
                "data": {
                    "notes": [
                        {
                            "display_title": "GEO 内容生成与分发链路测试说明",
                            "id": "6a977888000000001103bb96",
                        }
                    ]
                }
            }
        )
        published_tab = self._button()
        page = Mock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.get_by_text.side_effect = lambda text, exact: (
            _Candidates([published_tab]) if text == "已发布" else _Candidates([])
        )

        def register_response(_event, callback):
            callback(posted_response)

        page.on.side_effect = register_response

        result = await _reconcile_posted_note(
            page,
            "GEO 内容生成与分发链路测试说明",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["url"],
            "https://www.xiaohongshu.com/explore/6a977888000000001103bb96",
        )
        published_tab.click.assert_awaited_once()
        page.goto.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
