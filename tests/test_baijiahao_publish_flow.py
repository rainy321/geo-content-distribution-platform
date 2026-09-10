import unittest
from unittest.mock import AsyncMock, Mock, patch

from services.baijiahao_publish_flow import (
    _summarize_responses,
    click_exact_publish_and_observe,
)
from uploader.baijiahao_uploader.main import _launch_browser


class _Candidates:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _NoDialog:
    async def count(self):
        return 0


class _Request:
    method = "POST"


class _Response:
    request = _Request()
    url = "https://baijiahao.baidu.com/api/article/publish"
    status = 200

    def __init__(self, payload=None):
        self.payload = payload or {"status": "submitted"}

    async def json(self):
        return self.payload


class BaijiahaoPublishFlowTests(unittest.IsolatedAsyncioTestCase):
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

        with self.assertRaisesRegex(RuntimeError, "精确发布按钮数量异常"):
            await click_exact_publish_and_observe(page)

    async def test_accepted_submission_without_public_url_is_processing(self):
        button = self._button()
        page = Mock()
        page.url = "https://baijiahao.baidu.com/builder/rc/edit?type=news"
        page.get_by_role.return_value = _Candidates([button])
        page.locator.return_value = _NoDialog()
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()
        page.on = Mock(side_effect=lambda _event, callback: callback(_Response()))

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "processing")
        button.click.assert_awaited_once()

    async def test_target_close_after_click_keeps_accepted_response(self):
        button = self._button()
        page = Mock()
        page.url = "https://baijiahao.baidu.com/builder/rc/edit?type=news"
        page.get_by_role.return_value = _Candidates([button])
        page.wait_for_timeout = AsyncMock(
            side_effect=RuntimeError(
                "Target page, context or browser has been closed"
            )
        )
        page.wait_for_load_state = AsyncMock(side_effect=RuntimeError("Target closed"))
        page.remove_listener = Mock()
        page.on = Mock(side_effect=lambda _event, callback: callback(_Response()))

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "processing")
        self.assertIn("等待平台审核", result["message"])

    async def test_public_url_from_response_is_success(self):
        evidence = await _summarize_responses(
            [_Response({"url": "https://baijiahao.baidu.com/s?id=123456789"})]
        )

        self.assertEqual(
            evidence["public_url"],
            "https://baijiahao.baidu.com/s?id=123456789",
        )

    async def test_explicit_rejection_is_not_left_processing(self):
        button = self._button()
        page = Mock()
        page.url = "https://baijiahao.baidu.com/builder/rc/edit?type=news"
        page.get_by_role.return_value = _Candidates([button])
        page.locator.return_value = _NoDialog()
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()
        page.on = Mock(
            side_effect=lambda _event, callback: callback(
                _Response({"errno": 1001, "errmsg": "封面不能为空"})
            )
        )

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "failed")
        self.assertIn("封面不能为空", result["message"])
        button.click.assert_awaited_once()

    async def test_security_rejection_requires_manual_action(self):
        button = self._button()
        page = Mock()
        page.url = "https://baijiahao.baidu.com/builder/rc/edit?type=news"
        page.get_by_role.return_value = _Candidates([button])
        page.locator.return_value = _NoDialog()
        page.wait_for_timeout = AsyncMock()
        page.remove_listener = Mock()
        page.on = Mock(
            side_effect=lambda _event, callback: callback(
                _Response({"success": False, "message": "请完成人机验证"})
            )
        )

        result = await click_exact_publish_and_observe(page)

        self.assertEqual(result["status"], "need_action")
        self.assertIn("人机验证", result["message"])

    async def test_browser_launcher_falls_back_to_chrome_channel_without_stale_path(self):
        browser = object()
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(
            side_effect=[OSError("synthetic missing executable"), browser]
        )
        proxy = {"server": "http://127.0.0.1:7890"}

        with patch(
            "uploader.baijiahao_uploader.main.LOCAL_CHROME_PATH",
            r"C:\synthetic\chrome.exe",
        ):
            result = await _launch_browser(
                playwright,
                False,
                proxy_setting=proxy,
            )

        self.assertIs(result, browser)
        first_kwargs = playwright.chromium.launch.await_args_list[0].kwargs
        fallback_kwargs = playwright.chromium.launch.await_args_list[1].kwargs
        self.assertEqual(first_kwargs["executable_path"], r"C:\synthetic\chrome.exe")
        self.assertEqual(first_kwargs["proxy"], proxy)
        self.assertNotIn("executable_path", fallback_kwargs)
        self.assertEqual(fallback_kwargs["channel"], "chrome")
        self.assertEqual(fallback_kwargs["proxy"], proxy)


if __name__ == "__main__":
    unittest.main()
