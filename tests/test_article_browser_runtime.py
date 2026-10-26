"""Supported article paths use ordinary system browser configuration; no network."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services import browser_executable
from uploader.baijiahao_uploader import main as baijiahao
from uploader.xiaohongshu_uploader import main as xhs


CASES = (
    ('edge_only', '', 'synthetic-edge.exe', None, {'executable_path': 'synthetic-edge.exe'}),
    ('chrome_only', '', None, 'synthetic-chrome.exe', {'executable_path': 'synthetic-chrome.exe'}),
    ('explicit', 'synthetic-configured.exe', 'synthetic-edge.exe', 'synthetic-chrome.exe',
     {'executable_path': 'synthetic-configured.exe'}),
    ('no_system_browser', '', None, None, {}),
    ('both_installed_prefer_chrome', '', 'synthetic-edge.exe', 'synthetic-chrome.exe',
     {'executable_path': 'synthetic-chrome.exe'}),
)


def selection(module, case):
    _, configured, edge, chrome, _ = case
    stack = ExitStack()
    stack.enter_context(patch.object(module, 'LOCAL_CHROME_PATH', configured))
    stack.enter_context(patch.object(browser_executable, 'edge_executable', return_value=edge))
    stack.enter_context(patch.object(browser_executable, 'chrome_executable', return_value=chrome))
    return stack


def runtime():
    page = SimpleNamespace(url='https://creator.xiaohongshu.com/', goto=AsyncMock(),
                           wait_for_timeout=AsyncMock(), pause=AsyncMock(),
                           get_by_text=lambda *_: SimpleNamespace(count=AsyncMock(return_value=0)),
                           locator=lambda *_: SimpleNamespace(first=SimpleNamespace(count=AsyncMock(return_value=0))))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(),
                              storage_state=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    launch = AsyncMock(return_value=browser)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    manager = MagicMock()
    manager.__aenter__.return_value = playwright
    return SimpleNamespace(page=page, context=context, browser=browser, launch=launch,
                           playwright=playwright, manager=manager)


def note():
    instance = xhs.XiaoHongShuNote(image_paths=['synthetic-image.png'], note='synthetic body',
                                 tags=[], publish_date=0, account_file='synthetic-session.json',
                                 title='synthetic title', headless=False)
    instance.validate_upload_args = AsyncMock()
    instance.upload_note_content = AsyncMock()
    return instance


class ArticleBrowserSelectionTests(unittest.TestCase):
    def test_both_builders_use_same_explicit_edge_chrome_and_no_system_browser_rules(self):
        for module in (baijiahao, xhs):
            for case in CASES:
                with self.subTest(platform=module.__name__, browser=case[0]), selection(module, case):
                    for headless in (True, False):
                        options = module._build_launch_kwargs(headless)
                        self.assertIs(options['headless'], headless)
                        self.assertNotIn('channel', options)
                        self.assertEqual({key: value for key, value in options.items()
                                          if key in ('executable_path', 'channel')}, case[4])

    def test_builders_find_edge_and_chrome_in_isolated_install_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edge = root / 'Microsoft/Edge/Application/msedge.exe'
            chrome = root / 'Google/Chrome/Application/chrome.exe'
            for path in (edge, chrome):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic browser placeholder; never executed')
            with patch.dict('os.environ', {'ProgramFiles': directory}, clear=True):
                for expected in (chrome, edge, None):
                    for module in (baijiahao, xhs):
                        with self.subTest(platform=module.__name__, expected=expected), \
                                patch.object(module, 'LOCAL_CHROME_PATH', ''):
                            result = module._build_launch_kwargs(False)
                            self.assertEqual(result.get('executable_path'), str(expected) if expected else None)
                            self.assertNotIn('channel', result)
                    if expected is not None:
                        expected.unlink()


class ArticleBrowserRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_baijiahao_checker_and_article_launch_use_same_browser_for_all_configurations(self):
        for case in CASES:
            for entrypoint in ('checker', 'article'):
                env = runtime()
                with self.subTest(browser=case[0], entrypoint=entrypoint), selection(baijiahao, case), \
                        patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                        patch.object(baijiahao, 'set_init_script', new=AsyncMock(return_value=env.context)):
                    if entrypoint == 'checker':
                        self.assertTrue(await baijiahao.cookie_auth('synthetic-session.json'))
                        env.browser.new_context.assert_awaited_once_with(storage_state='synthetic-session.json')
                        env.context.close.assert_awaited_once()
                        env.browser.close.assert_awaited_once()
                    else:
                        # Stop the real article method at launch, before any editor/content operation.
                        env.launch.side_effect = RuntimeError('synthetic launch stopped before editor')
                        article = baijiahao.BaiJiaHaoArticle(title='synthetic title', body='synthetic body',
                                                           tags=[], publish_date=0,
                                                           account_file='synthetic-session.json',
                                                           cover_path='synthetic-image.png')
                        with self.assertRaisesRegex(RuntimeError, 'synthetic launch stopped'):
                            await article.upload(env.playwright)
                        env.browser.new_context.assert_not_awaited()
                    env.launch.assert_awaited_once()
                    options = env.launch.await_args.kwargs
                    self.assertEqual(options.get('executable_path'), case[4].get('executable_path'))
                    self.assertNotIn('channel', options)

    async def test_baijiahao_legacy_login_uses_same_explicit_or_installed_browser_selection(self):
        for case in CASES:
            env = runtime()
            with self.subTest(browser=case[0]), selection(baijiahao, case), \
                    patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                    patch.object(baijiahao, 'set_init_script', new=AsyncMock(return_value=env.context)):
                await baijiahao.baijiahao_cookie_gen('synthetic-session.json')
            env.launch.assert_awaited_once()
            self.assertEqual(env.launch.await_args.kwargs.get('executable_path'), case[4].get('executable_path'))
            self.assertNotIn('channel', env.launch.await_args.kwargs)

    async def test_baijiahao_explicit_launch_failure_never_falls_back_or_marks_cookie_expired(self):
        env = runtime()
        env.launch.side_effect = RuntimeError('synthetic explicitly configured browser is missing')
        with selection(baijiahao, CASES[2]), patch.object(baijiahao, 'async_playwright', return_value=env.manager):
            with self.assertRaisesRegex(RuntimeError, 'explicitly configured'):
                await baijiahao.cookie_auth('synthetic-session.json')
        env.launch.assert_awaited_once()
        self.assertEqual(env.launch.await_args.kwargs['executable_path'], 'synthetic-configured.exe')
        env.browser.new_context.assert_not_awaited()

    async def test_baijiahao_no_browser_failure_does_not_try_a_different_channel(self):
        env = runtime()
        env.launch.side_effect = RuntimeError('synthetic no browser installed')
        with selection(baijiahao, CASES[3]):
            with self.assertRaisesRegex(RuntimeError, 'no browser installed'):
                await baijiahao._launch_browser(env.playwright, True)
        env.launch.assert_awaited_once()
        self.assertNotIn('executable_path', env.launch.await_args.kwargs)
        self.assertNotIn('channel', env.launch.await_args.kwargs)

    async def test_baijiahao_context_initialization_failure_closes_browser_and_remains_an_error(self):
        env = runtime()
        env.browser.new_context.side_effect = RuntimeError('synthetic invalid session storage')
        with patch.object(baijiahao, 'async_playwright', return_value=env.manager):
            with self.assertRaisesRegex(RuntimeError, 'invalid session storage'):
                await baijiahao.cookie_auth('synthetic-session.json')
        env.browser.close.assert_awaited_once()
        env.page.goto.assert_not_awaited()

    async def test_baijiahao_context_preparation_failure_closes_context_and_browser(self):
        env = runtime()
        with patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                patch.object(baijiahao, 'set_init_script', new=AsyncMock(side_effect=RuntimeError('synthetic init failed'))):
            with self.assertRaisesRegex(RuntimeError, 'init failed'):
                await baijiahao.cookie_auth('synthetic-session.json')
        env.context.close.assert_awaited_once()
        env.browser.close.assert_awaited_once()

    async def test_baijiahao_navigation_error_is_not_a_false_expired_result(self):
        env = runtime()
        env.page.goto.side_effect = RuntimeError('synthetic network unavailable')
        with patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                patch.object(baijiahao, 'set_init_script', new=AsyncMock(return_value=env.context)):
            with self.assertRaisesRegex(RuntimeError, 'network unavailable'):
                await baijiahao.cookie_auth('synthetic-session.json')
        env.context.close.assert_awaited_once()
        env.browser.close.assert_awaited_once()

    async def test_baijiahao_visible_login_marker_is_still_false_and_closes_resources(self):
        env = runtime()
        env.page.get_by_text = lambda *_: SimpleNamespace(count=AsyncMock(return_value=1))
        with patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                patch.object(baijiahao, 'set_init_script', new=AsyncMock(return_value=env.context)):
            self.assertFalse(await baijiahao.cookie_auth('synthetic-session.json'))
        env.context.close.assert_awaited_once()
        env.browser.close.assert_awaited_once()

    async def test_baijiahao_context_close_failure_still_closes_browser(self):
        env = runtime()
        env.context.close.side_effect = RuntimeError('synthetic close failure')
        with patch.object(baijiahao, 'async_playwright', return_value=env.manager), \
                patch.object(baijiahao, 'set_init_script', new=AsyncMock(return_value=env.context)):
            with self.assertRaisesRegex(RuntimeError, 'close failure'):
                await baijiahao.cookie_auth('synthetic-session.json')
        env.browser.close.assert_awaited_once()

    async def test_xhs_checker_login_and_note_upload_share_all_browser_configurations(self):
        for case in CASES:
            for entrypoint in ('checker', 'login', 'note_upload'):
                env = runtime()
                with tempfile.TemporaryDirectory() as directory:
                    account_file = str(Path(directory) / 'synthetic-session.json')
                    Path(account_file).write_text('{}', encoding='utf-8')
                    with self.subTest(browser=case[0], entrypoint=entrypoint), selection(xhs, case), \
                            patch.object(xhs, 'async_playwright', return_value=env.manager), \
                            patch.object(xhs, 'set_init_script', new=AsyncMock(return_value=env.context)), \
                            patch.object(xhs, '_save_xhs_qrcode', new=AsyncMock(return_value={'image_path': str(Path(directory)/'synthetic-qr.png')})), \
                            patch.object(xhs, 'remove_qrcode_file', return_value=False):
                        if entrypoint == 'checker':
                            self.assertTrue(await xhs.cookie_auth(account_file))
                        elif entrypoint == 'login':
                            result = await xhs.xiaohongshu_cookie_gen(account_file, max_checks=0, headless=False)
                            self.assertEqual(result['status'], 'timeout')
                        else:
                            app = note()
                            await app.upload(env.playwright)
                            env.browser.new_context.assert_awaited_once_with(permissions=['geolocation'], storage_state=app.account_file)
                            app.upload_note_content.assert_awaited_once_with(env.page)
                            env.context.storage_state.assert_awaited_once_with(path=app.account_file)
                    env.launch.assert_awaited_once()
                    self.assertEqual(env.launch.await_args.kwargs.get('executable_path'), case[4].get('executable_path'))
                    self.assertNotIn('channel', env.launch.await_args.kwargs)
                    env.browser.close.assert_awaited_once()

    async def test_xhs_note_explicit_launch_failure_never_changes_browser_or_writes_content(self):
        env = runtime()
        env.launch.side_effect = RuntimeError('synthetic configured browser startup failure')
        app = note()
        with selection(xhs, CASES[2]):
            with self.assertRaisesRegex(RuntimeError, 'configured browser startup'):
                await app.upload(env.playwright)
        env.launch.assert_awaited_once_with(headless=False, executable_path='synthetic-configured.exe')
        env.browser.new_context.assert_not_awaited()
        app.upload_note_content.assert_not_awaited()

    async def test_xhs_note_context_initialization_failure_closes_browser_before_content_write(self):
        env = runtime()
        env.browser.new_context.side_effect = RuntimeError('synthetic invalid session storage')
        app = note()
        with self.assertRaisesRegex(RuntimeError, 'invalid session storage'):
            await app.upload(env.playwright)
        env.browser.close.assert_awaited_once()
        app.upload_note_content.assert_not_awaited()
        env.context.storage_state.assert_not_awaited()

    async def test_xhs_note_context_close_failure_still_closes_browser(self):
        env = runtime()
        env.context.close.side_effect = RuntimeError('synthetic context close error')
        with patch.object(xhs, 'set_init_script', new=AsyncMock(return_value=env.context)):
            with self.assertRaisesRegex(RuntimeError, 'context close error'):
                await note().upload(env.playwright)
        env.browser.close.assert_awaited_once()

    async def test_xhs_login_context_initialization_failure_closes_browser_and_reports_failure(self):
        env = runtime()
        env.browser.new_context.side_effect = RuntimeError('synthetic invalid context')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(xhs, 'async_playwright', return_value=env.manager):
            result = await xhs.xiaohongshu_cookie_gen(str(Path(directory)/'synthetic-session.json'), headless=False)
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'failed')
        env.browser.close.assert_awaited_once()
        env.page.goto.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
