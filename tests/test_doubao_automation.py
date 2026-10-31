"""Automatic web readiness and native orchestration; no real browser or network."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from services.desktop_geo_jobs import DesktopGeoJobs
from services.doubao_automation import inspect_available_doubao_session, _READY_JS
from services.doubao_source_collector import CollectorConfig, read_snapshot, _read_login_observation
from services.geo_research_probe_service import context
from services.geo_test_runner import RunConfig, run_test_plan, write_json
from services.geo_test_service import validate_plan
from tests.test_geo_tests import plan, answer


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = CollectorConfig(profile_dir=self.root / 'profile', output_root=self.root / 'evidence',
                                      headed=True, manual_wait_seconds=None, navigation_timeout_seconds=0.01)
        self.closed = False
        self.page = SimpleNamespace(url='https://www.doubao.com/chat/', is_closed=lambda: self.closed,
            evaluate=AsyncMock(return_value={'chat_interface_ready': True, 'model_label': '快速'}),
            fill=AsyncMock(side_effect=AssertionError('preflight must not fill')),
            press=AsyncMock(side_effect=AssertionError('preflight must not press')),
            click=AsyncMock(side_effect=AssertionError('preflight must not click')))
        self.browser_context = SimpleNamespace(pages=[self.page], close=AsyncMock())

    @asynccontextmanager
    async def factory(self):
        yield SimpleNamespace()

    async def inspect(self, observation, *, events=None, wait=None):
        values = list(observation)
        async def read(*args):
            return values.pop(0) if len(values) > 1 else values[0]
        with patch('services.doubao_automation.launch_context', AsyncMock(return_value=self.browser_context)), \
             patch('services.doubao_automation._navigate_to_doubao', AsyncMock()), \
             patch('services.doubao_automation._read_login_observation', read), \
             patch('services.doubao_automation._wait_for_manual_verification', wait or AsyncMock(side_effect=lambda *args, **kw: args[4])):
            return await inspect_available_doubao_session(self.config, playwright_factory=self.factory,
                                                          progress=events.append if events is not None else None)

    async def test_readiness_observes_actual_work_without_selecting_mode_or_submitting(self):
        work = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        result, path = await self.inspect([work, work])
        self.assertEqual(result['collection_status'], 'preflight_ready')
        self.assertEqual(result['actual_client_mode'], 'work')
        self.assertIsNone(result['environment']['requested_client_mode'])
        self.assertFalse(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['read_only'])
        for method in ('fill', 'press', 'click'):
            getattr(self.page, method).assert_not_awaited()
        self.browser_context.close.assert_awaited_once()

    async def test_unknown_mode_never_becomes_consumer(self):
        unknown = {'client_mode': None, 'login_required': False, 'challenge': False}
        result, _ = await self.inspect([unknown, unknown])
        self.assertEqual(result['failure_reason'], 'web_mode_unknown_not_submitted')
        self.assertIsNone(result['actual_client_mode'])

    async def test_spa_hydration_can_finish_before_the_finite_startup_budget(self):
        self.config = replace(self.config, navigation_timeout_seconds=2)
        unknown = {'client_mode': None, 'login_required': None, 'challenge': None}
        work = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        result, _ = await self.inspect([unknown, unknown, work, work])
        self.assertEqual(result['collection_status'], 'preflight_ready')
        self.assertEqual(result['actual_client_mode'], 'work')

    async def test_normal_redirect_during_ready_evaluation_retries_without_early_close(self):
        self.config = replace(self.config, navigation_timeout_seconds=2)
        work = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        seen = []
        async def ready(*args):
            self.assertEqual(self.browser_context.close.await_count, 0)
            seen.append(True)
            if len(seen) == 1:
                raise RuntimeError('Execution context was destroyed, most likely because of a navigation')
            return {'chat_interface_ready': True, 'model_label': '快速'}
        self.page.evaluate.side_effect = ready
        result, _ = await self.inspect([work, work, work, work])
        self.assertEqual(result['collection_status'], 'preflight_ready')
        self.assertEqual(len(seen), 2)
        self.browser_context.close.assert_awaited_once()

    async def test_normal_redirect_during_strict_observation_retries_without_early_close(self):
        self.config = replace(self.config, navigation_timeout_seconds=2)
        calls = []
        async def observe(*args):
            self.assertEqual(self.browser_context.close.await_count, 0)
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError('Cannot find context with specified id')
            return {'client_mode': 'work', 'login_required': False, 'challenge': False}
        with patch('services.doubao_automation.launch_context', AsyncMock(return_value=self.browser_context)), \
             patch('services.doubao_automation._navigate_to_doubao', AsyncMock()), \
             patch('services.doubao_automation._read_login_observation', observe):
            result, _ = await inspect_available_doubao_session(self.config, playwright_factory=self.factory)
        self.assertEqual(result['actual_client_mode'], 'work')
        self.assertEqual(len(calls), 3)
        self.page.fill.assert_not_awaited(); self.page.press.assert_not_awaited()

    async def test_persistent_navigation_interruptions_exhaust_startup_without_any_send(self):
        work = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        self.page.evaluate.side_effect = RuntimeError('Execution context was destroyed')
        result, _ = await self.inspect([work, work])
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertEqual(result['failure_reason'], 'web_navigation_not_settled_not_submitted')
        self.assertFalse(result['question_submission_attempted'])
        self.browser_context.close.assert_awaited_once()

    async def test_strict_collector_snapshots_do_not_default_unknown_mode_to_consumer(self):
        self.page.evaluate.side_effect = [
            {'client_mode': 'consumer', 'answer': 'synthetic'},
            {'client_mode': None, 'login_required': False, 'challenge': False},
        ]
        config = CollectorConfig(strict_client_mode=True)
        result = await read_snapshot(self.page, config)
        self.assertIsNone(result['client_mode'])
        self.assertEqual(result['answer'], 'synthetic')

    async def test_closed_window_never_allows_the_runner_to_continue(self):
        self.closed = True
        result, _ = await self.inspect([{'client_mode': 'consumer', 'login_required': False, 'challenge': False}])
        self.assertEqual(result['failure_reason'], 'manual_verification_window_closed')
        self.assertIsNone(result['actual_client_mode'])
        self.page.evaluate.assert_not_awaited()

    async def test_our_cleanup_does_not_relabel_a_network_failure_as_user_closure(self):
        async def close():
            self.closed = True
        self.browser_context.close.side_effect = close
        with patch('services.doubao_automation.launch_context', AsyncMock(return_value=self.browser_context)), \
             patch('services.doubao_automation._navigate_to_doubao', AsyncMock(side_effect=RuntimeError('net::ERR_CONNECTION_RESET'))):
            result, _ = await inspect_available_doubao_session(self.config, playwright_factory=self.factory)
        self.assertEqual(result['failure_reason'], 'network_error:ERR_CONNECTION_RESET')
        self.assertFalse(result['question_submission_attempted'])

    async def test_unknown_authentication_or_missing_composer_stops_without_send(self):
        for value in ({'client_mode': 'consumer', 'login_required': None, 'challenge': False},
                      {'client_mode': 'consumer', 'login_required': False, 'challenge': None}):
            result, _ = await self.inspect([value, value])
            self.assertEqual(result['failure_reason'], 'web_composer_not_ready_not_submitted')
        self.page.evaluate.return_value = {'chat_interface_ready': False, 'model_label': None}
        value = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        result, _ = await self.inspect([value, value])
        self.assertEqual(result['failure_reason'], 'web_composer_not_ready_not_submitted')

    async def test_existing_manual_wait_reports_same_request_and_rechecks_strict_mode(self):
        from services.doubao_source_collector import _wait_for_manual_verification
        events = []
        before = {'client_mode': 'work', 'login_required': True, 'challenge': False}
        after = {'client_mode': 'work', 'login_required': False, 'challenge': False}
        with patch('services.doubao_source_collector._save_checkpoint', AsyncMock()), \
             patch('services.doubao_source_collector.read_snapshot', AsyncMock(return_value=after)), \
             patch('services.doubao_source_collector.asyncio.sleep', AsyncMock()):
            result, _ = await self.inspect([before, after], events=events, wait=_wait_for_manual_verification)
        self.assertEqual(result['actual_client_mode'], 'work')
        self.assertEqual([event['event'] for event in events], ['manual_action_required', 'manual_action_cleared'])
        self.assertTrue(all(event['question_submission_attempted'] is False for event in events))

    async def test_cancellation_saves_no_submission_and_propagates(self):
        events = []
        before = {'client_mode': 'work', 'login_required': True, 'challenge': False}
        with self.assertRaises(asyncio.CancelledError):
            await self.inspect([before], events=events, wait=AsyncMock(side_effect=asyncio.CancelledError()))
        saved = [json.loads(path.read_text(encoding='utf-8')) for path in self.config.output_root.rglob('result.json')]
        self.assertEqual(saved[0]['failure_reason'], 'preflight_cancelled_not_submitted')
        self.assertFalse(saved[0]['question_submitted'])
        self.assertEqual(events[-1]['event'], 'collector_finished')


class BrowserReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_readiness_js_on_local_intercepted_bare_and_www_origins(self):
        from playwright.async_api import async_playwright
        html = ('<html><head><meta charset="utf-8"></head><body><button data-testid="workspace-switch-chip">豆包 工作</button>'
                '<button data-testid="chat_input_action_model">快速</button>'
                '<div class="tiptap" contenteditable="true"></div></body></html>')
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                requests = []
                async def fulfill(route):
                    requests.append(route.request.url)
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
                await context.route('**/*', fulfill)
                page = await context.new_page()
                for url, expected in [
                    ('https://doubao.com/chat/', True), ('https://www.doubao.com/chat/', True),
                    ('http://doubao.com/chat/', False), ('https://doubao.com.evil.invalid/chat/', False),
                    ('https://doubao.com/chatbot', False), ('https://doubao.com:8443/chat/', False),
                ]:
                    await page.goto(url)
                    result = await page.evaluate(_READY_JS, asdict(CollectorConfig().selectors))
                    self.assertEqual(result['chat_interface_ready'], expected, url)
                    strict = await _read_login_observation(page, CollectorConfig(strict_client_mode=True))
                    self.assertEqual(strict['client_mode'], 'work' if expected else None, url)
                    snapshot = await read_snapshot(page, CollectorConfig(strict_client_mode=True))
                    self.assertEqual(snapshot['client_mode'], 'work' if expected else None, url)
                    self.assertEqual(snapshot['chat_interface_ready'], expected, url)
                self.assertEqual(len(requests), 6)
            finally:
                await browser.close()


class AutomaticBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)
        self.preflights, self.collectors = [], []

    def finished(self):
        self.jobs.worker.join(timeout=5)
        self.assertFalse(self.jobs.worker.is_alive())
        return self.jobs.get_task()

    async def ready(self, config, *, progress):
        self.preflights.append((config, self.jobs.get_task()))
        return {'collection_status': 'preflight_ready', 'actual_client_mode': 'work'}, config.output_root / 'synthetic-result.json'

    async def runner(self, value, config, **kwargs):
        async def collect(collector, **_):
            self.collectors.append(collector)
            self.assertEqual(collector.client_mode, 'work')
            self.assertTrue(collector.strict_client_mode)
            return answer(collector.question, environment={'client_mode': collector.client_mode, 'model_label': '快速'}), self.root / 'answer.json'
        async def no_sleep(_):
            pass
        return await run_test_plan(value, config, collect=collect, sleep=no_sleep, **kwargs)

    def test_auto_run_locks_observed_mode_and_keeps_the_selected_profile(self):
        other = self.root / 'doubao/browser-profile'
        other.mkdir(parents=True)
        (other / 'private-marker').write_bytes(b'not-copied')
        value = plan()
        with patch('services.desktop_geo_jobs.inspect_available_doubao_session', self.ready), \
             patch('services.desktop_geo_jobs.run_test_plan', self.runner):
            ack = self.jobs.start_automatic_run(value, {'model_label': '快速'})
            result = self.finished()
        self.assertEqual(result['kind'], 'run')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(self.collectors), len(value['cases']))
        self.assertTrue(all(item.profile_dir == self.root / 'doubao/consumer-browser-profile' for item in self.collectors))
        self.assertFalse((self.collectors[0].profile_dir / 'private-marker').exists())
        self.assertEqual((other / 'private-marker').read_bytes(), b'not-copied')
        before = self.preflights[0][1]
        self.assertIsNone(before['requested_client_mode'])
        self.assertEqual(before['mode_policy'], 'available_web')
        self.assertEqual(before['automation_scope'], 'doubao_web')
        execution = result['result']['report']['execution']
        self.assertEqual(execution['client_mode'], 'work')
        self.assertEqual(execution['profile_mode'], 'consumer')
        self.assertEqual(execution['mode_policy'], 'available_web')
        self.assertEqual(result['requested_client_mode'], 'work')
        self.assertNotIn('cycle_id', result)
        self.assertEqual(result['id'], ack['id'])

    def test_auto_planner_shares_preflight_and_preserves_planning_role(self):
        async def planner(profile, args, config, **kwargs):
            self.assertEqual(config.client_mode, 'work')
            self.assertTrue(config.strict_client_mode)
            self.assertEqual(config.profile_dir, self.root / 'doubao/consumer-browser-profile')
            result = plan()
            result['planner'].update(client_mode='work', model_label='快速', role='question_planning_not_exposure_test')
            return result
        with patch('services.desktop_geo_jobs.inspect_available_doubao_session', self.ready), \
             patch('services.desktop_geo_jobs._plan_with_doubao', planner):
            self.jobs.plan_automatic(plan()['profile'], 4, {})
            result = self.finished()
        self.assertEqual(result['kind'], 'plan')
        self.assertEqual(result['status'], 'completed')
        value = validate_plan(result['result']['plan'])
        self.assertEqual(value['planner']['role'], 'question_planning_not_exposure_test')
        self.assertEqual(value['planner']['profile_mode'], 'consumer')
        self.assertEqual(value['planner']['client_mode'], 'work')

    def test_failed_or_unknown_preflight_does_not_invoke_runner_or_planner(self):
        for method in ('run', 'plan'):
            with patch('services.desktop_geo_jobs.inspect_available_doubao_session', AsyncMock(return_value=(
                {'collection_status': 'need_action', 'actual_client_mode': None, 'failure_reason': 'web_mode_unknown_not_submitted'},
                self.root / 'geo-tests/preflight/result.json'))), \
                 patch('services.desktop_geo_jobs.run_test_plan', AsyncMock()) as runner, \
                 patch('services.desktop_geo_jobs._plan_with_doubao', AsyncMock()) as planner:
                if method == 'run':
                    self.jobs.start_automatic_run(plan(), {})
                else:
                    self.jobs.plan_automatic(plan()['profile'], 4, {})
                result = self.finished()
            runner.assert_not_awaited(); planner.assert_not_awaited()
            self.assertEqual(result['status'], 'need_action')
            self.assertEqual(result['submission_state'], {'question_submission_attempted': False, 'question_submitted': False})
            self.assertIn('没有发送问题', result['message'])

    def test_automatic_options_reject_paths_and_invalid_types_before_starting(self):
        for options in ({'profile_dir': 'C:/other'}, {'client_mode': 'consumer'}, {'executable_path': 'other'},
                        {'profile_mode': 'phone'}, {'model_label': ['快速']}, None):
            with patch('services.desktop_geo_jobs.inspect_available_doubao_session', AsyncMock()) as inspect:
                self.assertIn('error', self.jobs.start_automatic_run(plan(), options))
                self.assertIn('error', self.jobs.plan_automatic(plan()['profile'], 4, options))
            inspect.assert_not_awaited()
        self.assertIsNone(self.jobs.worker)

    def test_retest_and_probe_keep_original_web_profile_policy(self):
        value = plan()
        execution = {'client_mode': 'work', 'requested_model_label': '快速', 'profile_mode': 'consumer',
                     'mode_policy': 'available_web', 'timeout_seconds': 180}
        baseline = {'execution': execution, 'cases': [{**case, 'client_mode': 'work'} for case in value['cases']]}
        cycle = {'test_plan': value, 'baseline': baseline}
        detail = context(cycle, value['cases'][0]['id'])
        self.assertEqual(detail['execution']['profile_mode'], 'consumer')
        self.assertEqual(detail['execution']['mode_policy'], 'available_web')
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=cycle), \
             patch('services.desktop_geo_jobs.geo_content_store.attach_retest'), \
             patch('services.desktop_geo_jobs.run_test_plan', self.runner):
            self.jobs.start_research_retest('synthetic-cycle')
            result = self.finished()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(all(item.profile_dir == self.root / 'doubao/consumer-browser-profile' for item in self.collectors))
        self.assertEqual(result['profile_mode'], 'consumer')

    def test_waiting_preflight_is_visible_cancellable_and_blocks_a_second_task(self):
        entered, closed = threading.Event(), threading.Event()
        async def waiting(config, *, progress):
            progress({'event': 'manual_action_required', 'reason': 'human_verification_required',
                'question_submission_attempted': False, 'question_submitted': False})
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
                progress({'event': 'collector_finished', 'collection_status': 'need_action',
                    'failure_reason': 'preflight_cancelled_not_submitted', 'failure_stage': 'web_preflight',
                    'question_submission_attempted': False, 'question_submitted': False})
        with patch('services.desktop_geo_jobs.inspect_available_doubao_session', waiting), \
             patch('services.desktop_geo_jobs.run_test_plan', AsyncMock()) as runner:
            self.jobs.start_automatic_run(plan(), {})
            self.assertTrue(entered.wait(timeout=3))
            current = self.jobs.get_task()
            self.assertTrue(current['waiting_for_user'])
            self.assertIsNone(current['requested_client_mode'])
            self.assertIn('error', self.jobs.start_automatic_run(plan(), {}))
            self.jobs.stop_task()
            result = self.finished()
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(closed.is_set())
        runner.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
