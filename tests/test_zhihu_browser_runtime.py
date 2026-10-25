"""Zhihu account, article and probe paths use ordinary local browser options."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services import browser_executable, zhihu_publication_verifier as verifier
from uploader.zhihu_uploader import main as zhihu


class ZhihuBrowserOptionsTests(unittest.TestCase):
    def test_existing_explicit_edge_chrome_choices_are_preserved_without_evasion_flags(self):
        cases = [('configured.exe', 'edge.exe', 'chrome.exe', 'configured.exe'),
                 ('', 'edge.exe', 'chrome.exe', 'edge.exe'),
                 ('', None, 'chrome.exe', 'chrome.exe'), ('', None, None, None)]
        for configured, edge, chrome, expected in cases:
            for module in (zhihu, verifier):
                with self.subTest(module=module.__name__, configured=configured, expected=expected), \
                        patch.object(module, 'LOCAL_CHROME_PATH', configured), \
                        patch.object(browser_executable, 'edge_executable', return_value=edge), \
                        patch.object(browser_executable, 'chrome_executable', return_value=chrome):
                    options = module._build_launch_kwargs(False) if module is zhihu else module._launch_options()
                    self.assertEqual(options.get('executable_path'), expected)
                    self.assertNotIn('channel', options)
                    self.assertEqual(options['args'], ['--lang=zh-CN', '--start-maximized']
                                     if module is zhihu else ['--lang=zh-CN'])
                    self.assertIs(options['headless'], module is verifier)


class ZhihuBrowserRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_checker_login_and_article_keep_normal_context_without_injected_scripts(self):
        for entrypoint in ('checker', 'login', 'article'):
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as directory:
                page = SimpleNamespace(url=zhihu.ZHIHU_WRITE_URL, goto=AsyncMock(), wait_for_timeout=AsyncMock(),
                    locator=lambda *_: SimpleNamespace(count=AsyncMock(return_value=1)))
                context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(),
                    add_init_script=AsyncMock(), storage_state=AsyncMock())
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
                launch = AsyncMock(return_value=browser)
                runtime = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
                manager = MagicMock()
                manager.__aenter__.return_value = runtime
                account = str(Path(directory)/'synthetic-session.json')
                with patch.object(zhihu, 'async_playwright', return_value=manager), \
                        patch.object(zhihu, 'browser_executable_options', return_value={'executable_path':'ordinary-browser.exe'}), \
                        patch.object(zhihu, '_wait_until_logged_in', new=AsyncMock(return_value=True)):
                    if entrypoint == 'checker':
                        self.assertTrue(await zhihu.cookie_auth(account))
                    elif entrypoint == 'login':
                        await zhihu.zhihu_cookie_gen(account)
                    else:
                        article = zhihu.ZhiHuArticle('说明', '正文', [], 0, account,
                            creation_statement=zhihu.ZHIHU_AI_CREATION_STATEMENT)
                        article.headless = False
                        article.fill_title = AsyncMock()
                        article.fill_body = AsyncMock()
                        article.apply_creation_statement = AsyncMock()
                        article.click_publish = AsyncMock()
                        await article.upload(runtime)
                        article.click_publish.assert_awaited_once_with(page)
                context.add_init_script.assert_not_awaited()
                launch.assert_awaited_once()
                self.assertEqual(launch.await_args.kwargs['executable_path'], 'ordinary-browser.exe')
                self.assertEqual(launch.await_args.kwargs['args'], ['--lang=zh-CN','--start-maximized'])
                browser.new_context.assert_awaited_once_with(**({} if entrypoint == 'login' else {'storage_state':account}))
                browser.close.assert_awaited_once()
