import builtins
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from db.createTable import initialize_database
from myUtils import login
from sau_backend import run_async_function
from services import browser_executable
from services.login_runtime import LoginCancelled, LoginStatusQueue, login_error_code, report_login_error
from uploader.zhihu_uploader import main as zhihu


def messages(queue):
    result = []
    while not queue.empty():
        result.append(queue.get_nowait())
    return result


class InstalledBrowserTests(unittest.TestCase):
    def test_installed_edge_then_chrome_matches_existing_geo_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edge = root / 'Microsoft/Edge/Application/msedge.exe'
            chrome = root / 'Google/Chrome/Application/chrome.exe'
            for path in (edge, chrome):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic executable placeholder')
            with patch.dict('os.environ', {'ProgramFiles': directory}, clear=True):
                self.assertEqual(browser_executable.browser_executable_options(), {'executable_path': str(edge)})
                edge.unlink()
                self.assertEqual(browser_executable.browser_executable_options(), {'executable_path': str(chrome)})
                chrome.unlink()
                self.assertEqual(browser_executable.browser_executable_options(), {})

    def test_explicit_browser_is_not_silently_replaced(self):
        with patch.object(browser_executable, 'edge_executable', side_effect=AssertionError('must respect configuration')):
            self.assertEqual(browser_executable.browser_executable_options('C:/configured/missing.exe'),
                             {'executable_path': 'C:/configured/missing.exe'})

    def test_login_and_zhihu_checker_share_browser_selection(self):
        for module, builder in ((login, login.get_browser_options), (zhihu, lambda: zhihu._build_launch_kwargs(False))):
            with self.subTest(module=module.__name__), patch.object(module, 'browser_executable_options', return_value={'executable_path': 'installed-edge.exe'}):
                self.assertEqual(builder()['executable_path'], 'installed-edge.exe')

    def test_failure_codes_do_not_forward_exception_details(self):
        cases = [
            (RuntimeError("BrowserType.launch: Executable doesn't exist at PRIVATE"), 'browser_not_found'),
            (RuntimeError('Target page, context or browser has been closed PRIVATE'), 'browser_closed'),
            (RuntimeError('BrowserType.launch: process failed PRIVATE'), 'browser_launch_failed'),
            (TimeoutError('PRIVATE'), 'login_timeout'),
            (LoginCancelled(), 'login_cancelled'),
            (ModuleNotFoundError('PRIVATE'), 'component_missing'),
            (RuntimeError('PRIVATE'), 'login_failed'),
        ]
        for exc, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(login_error_code(exc), expected)

    def test_worker_import_failure_has_safe_terminal_instead_of_heartbeat_forever(self):
        real_import = builtins.__import__
        def import_without_login(name, *args, **kwargs):
            if name == 'myUtils.login':
                raise ModuleNotFoundError('PRIVATE import detail', name='missing_component')
            return real_import(name, *args, **kwargs)
        queue = LoginStatusQueue()
        with patch('builtins.__import__', side_effect=import_without_login):
            run_async_function('9', 'synthetic account', queue)
        self.assertEqual(messages(queue), ['LOGIN_OPENING', 'COMPONENT_MISSING:missing_component',
                                          'LOGIN_ERROR:component_missing', '500'])

    def test_worker_return_without_terminal_is_a_failure(self):
        queue = LoginStatusQueue()
        with patch.object(login, 'zhihu_cookie_gen', new=AsyncMock(return_value=None)):
            run_async_function('9', 'synthetic account', queue)
        self.assertEqual(messages(queue), ['LOGIN_OPENING', 'LOGIN_ERROR:login_failed', '500'])

    def test_launch_diagnostic_stays_local_and_removes_profile_and_credentials(self):
        queue = Queue()
        exception = RuntimeError(
            'BrowserType.launch: process exited\nCall log:\n'
            r' - <launching> C:\Users\private-name\AppData\Edge\msedge.exe '
            '--proxy-server=http://proxy-user:proxy-password@example.invalid\n'
            'Cookie: PRIVATE_COOKIE\nAuthorization: PRIVATE_AUTH\ntoken=PRIVATE_TOKEN'
        )
        with self.assertLogs(level='WARNING') as log:
            report_login_error(queue, exception, stage='launch')
        local = '\n'.join(log.output)
        self.assertIn('stage=launch code=browser_launch_failed exception_type=RuntimeError', local)
        self.assertIn('Call log:', local)
        for secret in ('private-name', 'proxy-user', 'proxy-password', 'PRIVATE_COOKIE', 'PRIVATE_AUTH', 'PRIVATE_TOKEN'):
            self.assertNotIn(secret, local)
        self.assertEqual(messages(queue), ['LOGIN_ERROR:browser_launch_failed', '500'])

    def test_navigation_failure_does_not_log_exception_text(self):
        with self.assertLogs(level='WARNING') as log:
            report_login_error(Queue(), RuntimeError('PRIVATE_PAGE_CONTENT'), stage='navigation')
        self.assertNotIn('PRIVATE_PAGE_CONTENT', '\n'.join(log.output))


class ZhihuLoginRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / 'synthetic.db'
        self.cookies = self.root / 'synthetic-sessions'
        initialize_database(self.database)
        self.page = SimpleNamespace(goto=AsyncMock(), close=AsyncMock(),
                                    is_closed=lambda: False, url=zhihu.ZHIHU_LOGIN_URL,
                                    wait_for_timeout=AsyncMock())
        self.context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), close=AsyncMock(),
                                       cookies=AsyncMock(return_value=[]), storage_state=AsyncMock())
        self.browser = SimpleNamespace(new_context=AsyncMock(return_value=self.context), close=AsyncMock())
        self.launch = AsyncMock(return_value=self.browser)
        self.runtime = MagicMock()
        self.runtime.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=self.launch))
        self.queue = LoginStatusQueue()

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def run_login(self, *, login_completed=True, checker=True):
        waiter = AsyncMock(return_value=login_completed)
        with patch.object(login, 'async_playwright', return_value=self.runtime), \
                patch.object(login, 'set_init_script', new=AsyncMock(return_value=self.context)), \
                patch.object(login, 'check_cookie', new=AsyncMock(return_value=checker)) as check, \
                patch.object(zhihu, '_wait_until_logged_in', new=waiter):
            result = await login.zhihu_cookie_gen('synthetic account', self.queue,
                                                 database_path=self.database, cookies_directory=self.cookies)
        return result, waiter, check

    async def test_missing_browser_is_actionable_and_never_claims_window_opened(self):
        self.launch.side_effect = RuntimeError("BrowserType.launch: Executable doesn't exist at PRIVATE")
        result, waiter, check = await self.run_login()
        self.assertIsNone(result)
        self.assertEqual(messages(self.queue), ['LOGIN_ERROR:browser_not_found', '500'])
        waiter.assert_not_awaited()
        check.assert_not_awaited()
        self.assertFalse(self.cookies.exists())

    async def test_navigation_failure_is_safe_and_cleans_owned_window(self):
        self.page.goto.side_effect = RuntimeError('PRIVATE url or account detail')
        result, waiter, _ = await self.run_login()
        self.assertIsNone(result)
        self.assertEqual(messages(self.queue), ['LOGIN_ERROR:navigation_failed', '500'])
        waiter.assert_not_awaited()
        self.browser.close.assert_awaited_once()

    async def test_manual_state_only_follows_loaded_page_and_has_ten_minute_budget(self):
        async def navigate(*args, **kwargs):
            self.assertEqual(messages(self.queue), [])
        self.page.goto.side_effect = navigate
        result, waiter, check = await self.run_login(login_completed=False)
        self.assertIsNone(result)
        self.assertEqual(waiter.await_args.kwargs['timeout_ms'], 600_000)
        self.assertIs(waiter.await_args.kwargs['cancel_event'], self.queue.cancel_event)
        self.assertEqual(messages(self.queue), ['MANUAL_LOGIN', 'LOGIN_ERROR:login_timeout', '500'])
        check.assert_not_awaited()
        self.context.storage_state.assert_not_awaited()

    async def test_completed_manual_login_saves_injected_account_without_publishing(self):
        result, _, check = await self.run_login()
        self.assertTrue(result)
        self.assertEqual(messages(self.queue), ['MANUAL_LOGIN', '200'])
        self.assertEqual(check.await_args.args[0], 9)
        self.assertEqual(check.await_args.kwargs['cookies_directory'], self.cookies)
        self.context.storage_state.assert_awaited_once()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT type,userName,status FROM user_info').fetchall(), [(9, 'synthetic account', 1)])

    async def test_checker_runtime_failure_saves_manual_session_as_unverified(self):
        with patch.object(login, 'async_playwright', return_value=self.runtime), \
                patch.object(login, 'set_init_script', new=AsyncMock(return_value=self.context)), \
                patch.object(login, 'check_cookie', new=AsyncMock(side_effect=RuntimeError('PRIVATE runtime failure'))), \
                patch.object(zhihu, '_wait_until_logged_in', new=AsyncMock(return_value=True)):
            result = await login.zhihu_cookie_gen('synthetic account', self.queue,
                                                 database_path=self.database, cookies_directory=self.cookies)
        self.assertTrue(result)
        self.assertEqual(messages(self.queue), ['MANUAL_LOGIN', '200'])
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM user_info').fetchall(), [(0,)])

    async def test_cancel_after_authentication_does_not_save_session_or_account(self):
        async def cancel_after_detection(*args, **kwargs):
            self.queue.cancel_event.set()
            return True
        with patch.object(login, 'async_playwright', return_value=self.runtime), \
                patch.object(login, 'set_init_script', new=AsyncMock(return_value=self.context)), \
                patch.object(zhihu, '_wait_until_logged_in', new=AsyncMock(side_effect=cancel_after_detection)):
            result = await login.zhihu_cookie_gen('synthetic account', self.queue,
                                                 database_path=self.database, cookies_directory=self.cookies)
        self.assertIsNone(result)
        self.context.storage_state.assert_not_awaited()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM user_info').fetchone()[0], 0)
        self.assertEqual(messages(self.queue), ['MANUAL_LOGIN', 'LOGIN_ERROR:login_cancelled', '500'])

    async def test_manual_wait_stops_on_close_and_cancellation(self):
        self.page.is_closed = lambda: True
        with self.assertRaisesRegex(RuntimeError, 'has been closed'):
            await zhihu._wait_until_logged_in(self.page, self.context, cancel_event=self.queue.cancel_event)
        self.page.is_closed = lambda: False
        self.queue.cancel_event.set()
        with self.assertRaises(LoginCancelled):
            await zhihu._wait_until_logged_in(self.page, self.context, cancel_event=self.queue.cancel_event)

    async def test_manual_wait_checks_cancel_at_most_one_second_apart(self):
        waits = []
        async def wait(milliseconds):
            waits.append(milliseconds)
            self.queue.cancel_event.set()
        self.page.wait_for_timeout.side_effect = wait
        with self.assertRaises(LoginCancelled):
            await zhihu._wait_until_logged_in(self.page, self.context, cancel_event=self.queue.cancel_event)
        self.assertEqual(waits, [1000])

    async def test_checker_missing_runtime_propagates_for_account_status_service(self):
        self.launch.side_effect = RuntimeError("BrowserType.launch: Executable doesn't exist at PRIVATE")
        with patch.object(zhihu, 'async_playwright', return_value=self.runtime):
            with self.assertRaisesRegex(RuntimeError, "Executable doesn't exist"):
                await zhihu.cookie_auth(self.cookies / 'synthetic.json')


if __name__ == '__main__':
    unittest.main()
