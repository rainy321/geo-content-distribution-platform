import unittest
from unittest.mock import AsyncMock, Mock

from services.sohu_publish_flow import (
    _summarize_submission_responses,
    click_exact_publish_and_observe,
)


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


class _EmptyLocator:
    async def count(self):
        return 0


class _Request:
    method = "POST"


class _Response:
    request = _Request()
    url = "https://mp.sohu.com/api/news/publish"
    status = 200

    def __init__(self, payload=None):
        self.payload = payload or {"code": 0, "status": "submitted"}

    async def json(self):
        return self.payload


class SohuPublishFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _role_lookup(mapping):
        return lambda _role, name, exact: _CandidateList(mapping.get(name, []))

    @staticmethod
    def _button():
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()
        return button

    @staticmethod
    def _sohu_publish_control():
        control = SohuPublishFlowTests._button()
        control.inner_text = AsyncMock(return_value="发布")
        return control

    async def test_requires_one_exact_publish_button(self):
        page = Mock()
        page.get_by_role.side_effect = self._role_lookup({})
        page.locator.return_value = _EmptyLocator()
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "精确发布控件数量异常"):
            await click_exact_publish_and_observe(page)

        labels = {call.kwargs["name"] for call in page.get_by_role.call_args_list}
        self.assertEqual(labels, {"发布", "立即发布"})

    async def test_accepts_observed_non_button_publish_control(self):
        control = self._sohu_publish_control()
        page = Mock()
        page.url = "https://mp.sohu.com/mpfe/v4/contentManagement/news/list"
        page.get_by_role.side_effect = self._role_lookup({})

        def locate(selector):
            if "content-button-commit" in selector:
                return _CandidateList([control])
            return _DialogList()

        page.locator.side_effect = locate
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()

        def register(_event, callback):
            callback(_Response())

        page.on = Mock(side_effect=register)

        result = await click_exact_publish_and_observe(page)

        control.click.assert_awaited_once()
        self.assertEqual(result["status"], "processing")

    async def test_accepted_submission_without_public_url_is_processing(self):
        button = self._button()
        page = Mock()
        page.url = "https://mp.sohu.com/mpfe/v4/contentManagement/news/list"
        page.get_by_role.side_effect = self._role_lookup({"发布": [button]})
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

    async def test_public_article_navigation_is_success(self):
        button = self._button()
        page = Mock()
        page.url = "https://www.sohu.com/a/812345678_121234567/"
        page.get_by_role.side_effect = self._role_lookup({"立即发布": [button]})
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertTrue(result["success"])
        self.assertEqual(result["url"], page.url)

    async def test_draft_save_is_not_accepted_submission(self):
        evidence = await _summarize_submission_responses(
            [_Response({"code": 0, "message": "保存成功"})]
        )

        self.assertFalse(evidence["accepted"])
        self.assertEqual(evidence["public_url"], "")

    async def test_public_url_from_response_is_extracted(self):
        evidence = await _summarize_submission_responses(
            [_Response({"data": {"url": "https://www.sohu.com/a/812345678_121234567"}})]
        )

        self.assertEqual(
            evidence["public_url"],
            "https://www.sohu.com/a/812345678_121234567",
        )


if __name__ == "__main__":
    unittest.main()
