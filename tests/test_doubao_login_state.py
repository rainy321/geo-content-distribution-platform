import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.doubao_source_collector import CollectorConfig, _read_login_observation, open_doubao_session


class LoginObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_safe_typed_fields_leave_the_login_reader(self):
        page = SimpleNamespace(evaluate=AsyncMock(return_value={
            'client_mode': 'work', 'login_required': False, 'challenge': True,
            'account': 'synthetic-private-account', 'response': 'synthetic-private-answer',
        }))
        self.assertEqual(await _read_login_observation(page, CollectorConfig()), {
            'client_mode': 'work', 'login_required': False, 'challenge': True,
        })
        page.evaluate.return_value = {'client_mode': 'unrecognized', 'login_required': 0, 'challenge': 'false'}
        self.assertEqual(await _read_login_observation(page, CollectorConfig()), {
            'client_mode': None, 'login_required': None, 'challenge': None,
        })

    def login_factory(self, *, observation=None):
        events, browser_events = {}, {}
        state = {'closed': False, 'connected': True}
        page = SimpleNamespace(
            goto=AsyncMock(), is_closed=lambda: state['closed'],
            on=lambda event, callback: events.update({event: callback}),
            evaluate=AsyncMock(return_value=observation or {
                'client_mode': 'work', 'login_required': False, 'challenge': False,
            }),
        )
        browser = SimpleNamespace(on=lambda event, callback: browser_events.update({event: callback}),
                                  is_connected=lambda: state['connected'])
        context = SimpleNamespace(pages=[page], browser=browser, close=AsyncMock())

        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context)))

        return factory, page, context, state, events, browser_events

    async def run_login(self, factory):
        with tempfile.TemporaryDirectory() as directory:
            return await open_doubao_session(CollectorConfig(profile_dir=Path(directory)/'profile'),
                                             wait_seconds=None, playwright_factory=factory)

    async def test_manual_close_keeps_last_observed_mode_without_claiming_login_or_save(self):
        factory, page, context, state, _, _ = self.login_factory()

        async def close_page(_seconds):
            state['closed'] = True

        with patch('services.doubao_source_collector.asyncio.sleep', side_effect=close_page):
            result = await self.run_login(factory)
        self.assertEqual(result, {'client_mode': 'work', 'login_required': False, 'challenge': False,
                                  'close_reason': 'window_closed', 'session_saved': None})
        page.evaluate.assert_awaited_once()
        context.close.assert_awaited_once()

    async def test_page_crash_ends_manual_wait_and_keeps_save_unknown(self):
        factory, page, context, _, events, _ = self.login_factory()
        # Persistent contexts may expose no Browser object; page crash still ends the wait.
        context.browser = None

        async def crash_page(_seconds):
            events['crash'](page)

        with patch('services.doubao_source_collector.asyncio.sleep', side_effect=crash_page):
            result = await self.run_login(factory)
        self.assertEqual(result['close_reason'], 'page_crashed')
        self.assertEqual(result['client_mode'], 'work')
        self.assertIsNone(result['session_saved'])
        context.close.assert_awaited_once()

    async def test_disconnect_during_observation_finishes_without_inventing_a_login_state(self):
        factory, page, context, state, _, browser_events = self.login_factory()

        async def disconnect(*_args):
            state['connected'] = False
            browser_events['disconnected'](context.browser)
            raise RuntimeError('Target page, context or browser has been closed')

        page.evaluate.side_effect = disconnect
        context.close.side_effect = RuntimeError('Target page, context or browser has been closed')
        with patch('services.doubao_source_collector.asyncio.sleep', new_callable=AsyncMock):
            result = await self.run_login(factory)
        self.assertEqual(result, {'client_mode': None, 'login_required': None, 'challenge': None,
                                  'close_reason': 'browser_disconnected', 'session_saved': None})

    async def test_page_closed_during_startup_is_a_normal_unknown_result(self):
        factory, page, context, state, _, _ = self.login_factory()

        async def close_during_navigation(*_args, **_kwargs):
            state['closed'] = True
            raise RuntimeError('Target page, context or browser has been closed')

        page.goto.side_effect = close_during_navigation
        result = await self.run_login(factory)
        self.assertEqual(result['close_reason'], 'window_closed')
        self.assertIsNone(result['client_mode'])
        self.assertIsNone(result['login_required'])
        self.assertIsNone(result['session_saved'])
        page.evaluate.assert_not_awaited()
        context.close.assert_awaited_once()

    async def test_navigation_interruption_preserves_only_the_last_actual_observation(self):
        factory, page, _, state, _, _ = self.login_factory()
        page.evaluate.side_effect = [
            {'client_mode': 'consumer', 'login_required': True, 'challenge': False},
            RuntimeError('Execution context was destroyed, most likely because of a navigation'),
        ]
        sleeps = 0

        async def close_after_navigation(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                state['closed'] = True

        with patch('services.doubao_source_collector.asyncio.sleep', side_effect=close_after_navigation):
            result = await self.run_login(factory)
        self.assertEqual(result['client_mode'], 'consumer')
        self.assertTrue(result['login_required'])
        self.assertFalse(result['challenge'])
        self.assertIsNone(result['session_saved'])


if __name__ == '__main__':
    unittest.main()
