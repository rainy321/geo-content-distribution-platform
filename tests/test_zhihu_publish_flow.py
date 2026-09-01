import unittest
from unittest.mock import AsyncMock, Mock

from services.zhihu_publish_flow import click_exact_publish_and_observe


class _CandidateList:
    def __init__(self, candidates):
        self.candidates = candidates

    async def count(self):
        return len(self.candidates)

    def nth(self, index):
        return self.candidates[index]


class _DialogList:
    async def count(self):
        return 0


class ZhihuPublishFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_one_exact_publish_button(self):
        page = Mock()
        page.get_by_role.return_value = _CandidateList([])
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "精确发布按钮数量异常"):
            await click_exact_publish_and_observe(page)

        page.get_by_role.assert_called_once_with(
            "button", name="发布", exact=True
        )

    async def test_exact_click_without_submission_is_not_reported_as_success(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://zhuanlan.zhihu.com/write"
        page.get_by_role.return_value = _CandidateList([button])
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "未观察到文章提交请求"):
            await click_exact_publish_and_observe(page)

        button.click.assert_awaited_once_with(timeout=5_000)

    async def test_navigation_to_article_is_success_not_context_failure(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://zhuanlan.zhihu.com/p/2078070222595035345"
        page.get_by_role.return_value = _CandidateList([button])
        page.wait_for_timeout = AsyncMock(
            side_effect=RuntimeError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        )
        page.wait_for_load_state = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["url"],
            "https://zhuanlan.zhihu.com/p/2078070222595035345",
        )


if __name__ == "__main__":
    unittest.main()
