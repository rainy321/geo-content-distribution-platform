import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from myUtils.auth import cookie_auth_toutiao
from uploader.toutiao_uploader import main as toutiao


class ToutiaoAuthLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_route_uses_maintained_checker_without_legacy_browser(self):
        account_file = Path('configured-cookies/account.json')
        with patch('myUtils.auth.async_playwright', side_effect=AssertionError('legacy browser must not launch')):
            for connected in (True, False):
                with patch.object(toutiao, 'cookie_auth', new=AsyncMock(return_value=connected)) as checker:
                    self.assertIs(await cookie_auth_toutiao(account_file), connected)
                    checker.assert_awaited_once_with(account_file)

    async def _check_with_fake_browser(self, *, url, navigation_error=None, context_error=None):
        page = SimpleNamespace(
            url=url,
            goto=AsyncMock(side_effect=navigation_error),
            wait_for_timeout=AsyncMock(),
            locator=lambda _selector: SimpleNamespace(count=AsyncMock(return_value=0)),
        )
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context, side_effect=context_error),
            close=AsyncMock(),
        )
        launch = AsyncMock(return_value=browser)
        runtime = MagicMock()
        runtime.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        account_file = Path('isolated-cookies/current.json')
        with patch.object(toutiao, 'async_playwright', return_value=runtime), \
                patch.object(toutiao, 'set_init_script', new=AsyncMock(return_value=context)), \
                patch.object(toutiao, 'LOCAL_CHROME_PATH', 'C:/configured/browser.exe'), \
                patch.object(toutiao, 'LOCAL_CHROME_HEADLESS', True):
            result = await cookie_auth_toutiao(account_file)
        self.assertEqual(launch.await_args.kwargs['executable_path'], 'C:/configured/browser.exe')
        self.assertTrue(launch.await_args.kwargs['headless'])
        browser.new_context.assert_awaited_once_with(storage_state=account_file)
        browser.close.assert_awaited_once()
        runtime.__aexit__.assert_awaited_once()
        return result

    async def test_valid_dashboard_closes_browser_and_uses_configured_runtime(self):
        self.assertTrue(await self._check_with_fake_browser(url='https://mp.toutiao.com/profile_v4/index'))

    async def test_expired_session_closes_browser_without_claiming_connected(self):
        self.assertFalse(await self._check_with_fake_browser(url='https://mp.toutiao.com/auth/page/login'))

    async def test_network_error_still_closes_browser(self):
        self.assertFalse(await self._check_with_fake_browser(url='about:blank', navigation_error=TimeoutError('synthetic navigation timeout')))

    async def test_invalid_storage_state_still_closes_browser(self):
        self.assertFalse(await self._check_with_fake_browser(url='about:blank', context_error=ValueError('synthetic invalid session')))


if __name__ == '__main__':
    unittest.main()
