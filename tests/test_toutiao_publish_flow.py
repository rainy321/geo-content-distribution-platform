import unittest
from unittest.mock import AsyncMock, Mock

from services.toutiao_publish_flow import (
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


class _Request:
    method = "POST"


class _Response:
    request = _Request()
    url = "https://mp.toutiao.com/api/graphic/publish"
    status = 200

    async def json(self):
        return {"code": 0, "status": "submitted"}


class _PayloadResponse(_Response):
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class ToutiaoPublishFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _role_lookup(mapping):
        return lambda _role, name, exact: _CandidateList(mapping.get(name, []))

    async def test_requires_one_exact_publish_button(self):
        page = Mock()
        page.get_by_role.side_effect = self._role_lookup({})
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "精确首阶段发布按钮数量异常"):
            await click_exact_publish_and_observe(page)

        requested_labels = {call.kwargs["name"] for call in page.get_by_role.call_args_list}
        self.assertEqual(requested_labels, {"预览并发布", "发布"})

    async def test_accepted_submission_without_public_url_is_processing(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://mp.toutiao.com/profile_v4/graphic/articles"
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

    async def test_draft_save_success_is_not_submission_acceptance(self):
        evidence = await _summarize_submission_responses(
            [
                _PayloadResponse(
                    {
                        "code": 0,
                        "err_no": 0,
                        "message": "保存成功",
                        "reason": "保存成功",
                    }
                )
            ]
        )

        self.assertEqual(evidence["request_count"], 1)
        self.assertFalse(evidence["accepted"])
        self.assertEqual(evidence["public_url"], "")

    async def test_explicit_submission_message_is_accepted(self):
        evidence = await _summarize_submission_responses(
            [_PayloadResponse({"code": 0, "message": "提交成功，等待审核"})]
        )

        self.assertTrue(evidence["accepted"])

    async def test_navigation_to_public_article_is_success(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://www.toutiao.com/article/123456789/"
        page.get_by_role.side_effect = self._role_lookup({"发布": [button]})
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

    async def test_two_stage_preview_and_confirm_submission(self):
        preview_button = Mock()
        preview_button.is_visible = AsyncMock(return_value=True)
        preview_button.is_enabled = AsyncMock(return_value=True)
        preview_button.click = AsyncMock()
        confirm_button = Mock()
        confirm_button.is_visible = AsyncMock(return_value=True)
        confirm_button.is_enabled = AsyncMock(return_value=True)
        confirm_button.click = AsyncMock()

        calls = {"preview": 0}

        def role_lookup(_role, name, exact):
            if name == "预览并发布":
                calls["preview"] += 1
                return _CandidateList([preview_button] if calls["preview"] == 1 else [])
            if name == "确认发布":
                return _CandidateList([confirm_button])
            return _CandidateList([])

        page = Mock()
        page.url = "https://mp.toutiao.com/profile_v4/graphic/articles"
        page.get_by_role.side_effect = role_lookup
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()

        def register(_event, callback):
            callback(_Response())

        page.on = Mock(side_effect=register)

        result = await click_exact_publish_and_observe(page)

        preview_button.click.assert_awaited_once()
        confirm_button.click.assert_awaited_once()
        self.assertEqual(result["status"], "processing")

    async def test_two_stage_refuses_ambiguous_confirmation(self):
        preview_button = Mock()
        preview_button.is_visible = AsyncMock(return_value=True)
        preview_button.is_enabled = AsyncMock(return_value=True)
        preview_button.click = AsyncMock()
        confirm_buttons = []
        for _ in range(2):
            button = Mock()
            button.is_visible = AsyncMock(return_value=True)
            button.is_enabled = AsyncMock(return_value=True)
            confirm_buttons.append(button)

        def role_lookup(_role, name, exact):
            if name == "预览并发布":
                return _CandidateList([preview_button])
            if name == "确认发布":
                return _CandidateList(confirm_buttons)
            return _CandidateList([])

        page = Mock()
        page.get_by_role.side_effect = role_lookup
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "确认发布按钮数量异常"):
            await click_exact_publish_and_observe(page)

        for button in confirm_buttons:
            button.click.assert_not_called()

    async def test_two_stage_uses_exact_css_fallback_for_current_editor(self):
        preview_button = Mock()
        preview_button.is_visible = AsyncMock(return_value=True)
        preview_button.is_enabled = AsyncMock(return_value=True)
        preview_button.click = AsyncMock()
        confirm_button = Mock()
        confirm_button.is_visible = AsyncMock(return_value=True)
        confirm_button.is_enabled = AsyncMock(return_value=True)
        confirm_button.inner_text = AsyncMock(return_value="确认并发布")
        confirm_button.click = AsyncMock()

        page = Mock()
        page.url = "https://mp.toutiao.com/profile_v4/graphic/articles"
        page.get_by_role.side_effect = self._role_lookup(
            {"预览并发布": [preview_button]}
        )
        page.locator.side_effect = lambda selector: (
            _CandidateList([confirm_button])
            if selector == "button.publish-btn-last"
            else _DialogList()
        )
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()

        def register(_event, callback):
            callback(_Response())

        page.on = Mock(side_effect=register)

        result = await click_exact_publish_and_observe(page)

        confirm_button.click.assert_awaited_once()
        self.assertEqual(result["status"], "processing")

    async def test_css_fallback_rejects_still_visible_preview_button(self):
        preview_button = Mock()
        preview_button.is_visible = AsyncMock(return_value=True)
        preview_button.is_enabled = AsyncMock(return_value=True)
        preview_button.inner_text = AsyncMock(return_value="预览并发布")
        preview_button.click = AsyncMock()

        page = Mock()
        page.get_by_role.side_effect = self._role_lookup(
            {"预览并发布": [preview_button]}
        )
        page.locator.side_effect = lambda selector: (
            _CandidateList([preview_button])
            if selector == "button.publish-btn-last"
            else _DialogList()
        )
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "未找到唯一可用"):
            await click_exact_publish_and_observe(page)

        preview_button.click.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
