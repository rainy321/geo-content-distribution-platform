import unittest
from unittest.mock import AsyncMock, Mock

from services.toutiao_publish_flow import click_exact_publish_and_observe


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


class _Request:
    method = "POST"


class _Response:
    request = _Request()
    url = "https://mp.toutiao.com/api/graphic/publish"
    status = 200

    async def json(self):
        return {"code": 0, "status": "submitted"}


class ToutiaoPublishFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_accepted_submission_without_public_url_is_processing(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://mp.toutiao.com/profile_v4/graphic/articles"
        page.get_by_role.return_value = _CandidateList([button])
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()

        def register(_event, callback):
            callback(_Response())

        page.on = Mock(side_effect=register)

        result = await click_exact_publish_and_observe(page)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "processing")
        self.assertIn("等待平台审核", result["message"])

    async def test_navigation_to_public_article_is_success(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://www.toutiao.com/article/123456789/"
        page.get_by_role.return_value = _CandidateList([button])
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["url"],
            "https://www.toutiao.com/article/123456789/",
        )


if __name__ == "__main__":
    unittest.main()
