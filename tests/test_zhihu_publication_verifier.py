import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services import zhihu_publication_verifier as verifier

from services.zhihu_publication_verifier import (
    _normalize_article,
    _safe_get,
)


class ZhihuPublicationVerifierTests(unittest.TestCase):
    def test_normalizes_article_url_to_https(self):
        article = _normalize_article(
            {
                "id": "2078070222595035345",
                "title": "测试标题",
                "url": "http://zhuanlan.zhihu.com/p/2078070222595035345",
                "created": 123,
            }
        )

        self.assertEqual(
            article["url"],
            "https://zhuanlan.zhihu.com/p/2078070222595035345",
        )
        self.assertEqual(article["title"], "测试标题")

    def test_builds_url_when_api_omits_it(self):
        article = _normalize_article({"id": "123", "title": "测试"})

        self.assertEqual(article["url"], "https://zhuanlan.zhihu.com/p/123")


class ZhihuPublicationVerifierAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.account_file = Path(self.directory.name) / 'synthetic-session.json'
        self.account_file.write_text('{}', encoding='utf-8')
        self.request = SimpleNamespace(get=AsyncMock(side_effect=[
            SimpleNamespace(ok=True, json=AsyncMock(return_value={'url_token': 'synthetic-member'})),
            SimpleNamespace(ok=True, json=AsyncMock(return_value={'data': []})),
        ]))
        self.context = SimpleNamespace(request=self.request, close=AsyncMock())
        self.browser = SimpleNamespace(new_context=AsyncMock(return_value=self.context), close=AsyncMock())
        self.launch = AsyncMock(return_value=self.browser)
        self.runtime = MagicMock()
        self.runtime.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_unconfigured_browser_uses_shared_installed_browser_selection(self):
        with patch.object(verifier, 'LOCAL_CHROME_PATH', ''), \
                patch.object(verifier, 'browser_executable_options', return_value={'executable_path': 'synthetic-edge.exe'}) as chooser, \
                patch.object(verifier, 'async_playwright', return_value=self.runtime):
            self.assertIsNone(await verifier.find_public_zhihu_article('synthetic title', self.account_file))
        chooser.assert_called_once_with('')
        self.assertEqual(self.launch.await_args.kwargs['executable_path'], 'synthetic-edge.exe')
        self.assertTrue(self.launch.await_args.kwargs['headless'])
        self.context.close.assert_awaited_once()
        self.browser.close.assert_awaited_once()

    async def test_context_initialization_failure_still_closes_browser(self):
        self.browser.new_context.side_effect = RuntimeError('synthetic invalid storage state')
        with patch.object(verifier, 'async_playwright', return_value=self.runtime):
            with self.assertRaisesRegex(RuntimeError, 'synthetic invalid storage'):
                await verifier.find_public_zhihu_article('synthetic title', self.account_file)
        self.browser.close.assert_awaited_once()
        self.context.close.assert_not_awaited()
        self.request.get.assert_not_awaited()

    async def test_context_close_failure_closes_browser_and_does_not_allow_publish(self):
        self.context.close.side_effect = RuntimeError('synthetic close failure')
        with patch.object(verifier, 'async_playwright', return_value=self.runtime):
            with self.assertRaisesRegex(RuntimeError, 'synthetic close failure'):
                await verifier.find_public_zhihu_article('synthetic title', self.account_file)
        self.browser.close.assert_awaited_once()

    async def test_missing_launch_executable_remains_failure_before_any_public_query(self):
        self.launch.side_effect = RuntimeError("BrowserType.launch: Executable doesn't exist at PRIVATE_PATH")
        with patch.object(verifier, 'async_playwright', return_value=self.runtime):
            with self.assertRaisesRegex(RuntimeError, "Executable doesn't exist"):
                await verifier.find_public_zhihu_article('synthetic title', self.account_file)
        self.browser.new_context.assert_not_awaited()
        self.request.get.assert_not_awaited()

    async def test_network_error_is_sanitized(self):
        class LeakyRequestContext:
            async def get(self, *_args, **_kwargs):
                raise RuntimeError("cookie: private-session-token-must-not-appear")

        with self.assertRaisesRegex(RuntimeError, "网络异常：RuntimeError") as caught:
            await _safe_get(
                LeakyRequestContext(),
                "https://www.zhihu.com/api/v4/me",
                stage="身份接口",
            )

        self.assertNotIn("private-session-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
