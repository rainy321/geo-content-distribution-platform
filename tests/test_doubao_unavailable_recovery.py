"""Local DOM and async fixtures only; no live platform, credentials or data."""
import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services import doubao_source_collector as collector
from services.desktop_geo_jobs import DesktopGeoJobs
from services.doubao_task_recovery import recovery_message
from services.geo_test_runner import run_test_plan
from services.geo_test_service import build_report
from tests.test_geo_tests import answer, plan


ERROR_HTML = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>豆包</title><body>
<main><h2>该页面暂时不可用</h2><p>这可能是一个技术错误，我们正在努力解决。</p>
<p>请尝试刷新此页面。</p></main></body></html>'''


class WebsiteUnavailableDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        self.browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        await self.page.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html', body=ERROR_HTML))
        await self.page.goto('https://www.doubao.com/chat/')
        self.config = collector.CollectorConfig(question='合成本地问题')

    async def test_whole_page_error_has_specific_reason_without_guessing_capacity_or_login(self):
        value = await collector.read_snapshot(self.page, self.config)
        self.assertTrue(value['website_temporarily_unavailable'])
        self.assertFalse(value['service_unavailable'])
        self.assertFalse(value['login_required'])
        self.assertEqual(collector._blocked_reason(value), 'website_temporarily_unavailable')

    async def test_answer_and_user_quotes_never_identify_a_site_error(self):
        for role in ('assistant', 'user'):
            with self.subTest(role=role):
                await self.page.set_content(ERROR_HTML.replace('<main>', f'<main data-message-role="{role}">'))
                value = await collector.read_snapshot(self.page, self.config)
                self.assertFalse(value['website_temporarily_unavailable'])
                self.assertIsNone(collector._blocked_reason(value))

    async def test_hidden_error_or_working_chat_is_not_whole_page_unavailable(self):
        await self.page.set_content(ERROR_HTML.replace('<main>', '<main hidden>'))
        self.assertFalse((await collector.read_snapshot(self.page, self.config))['website_temporarily_unavailable'])
        await self.page.set_content(ERROR_HTML.replace('</body>', '''
            <button data-testid="chat_input_action_model">合成模型</button>
            <div contenteditable="true" class="tiptap">正常编辑器</div></body>'''))
        value = await collector.read_snapshot(self.page, self.config)
        self.assertTrue(value['chat_interface_ready'])
        self.assertFalse(value['website_temporarily_unavailable'])

    async def test_visible_human_verification_still_has_priority(self):
        await self.page.set_content(ERROR_HTML.replace('</body>', '<div id="captcha">请完成安全验证</div></body>'))
        value = await collector.read_snapshot(self.page, self.config)
        self.assertEqual(collector._blocked_reason(value), 'human_verification_required')


class NavigationFailureEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    async def collect_failed_navigation(self, *, unavailable):
        page = SimpleNamespace(is_closed=Mock(return_value=False), screenshot=AsyncMock(), locator=Mock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        config = collector.CollectorConfig(question='合成本地导航问题', headed=True, manual_wait_seconds=None,
            output_root=self.root/'evidence', profile_dir=self.root/'profile')
        navigate = AsyncMock(side_effect=TimeoutError('synthetic navigation timeout'))
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', navigate), \
             patch.object(collector, 'read_snapshot', AsyncMock(return_value={
                 'website_temporarily_unavailable': unavailable})):
            result, path = await collector.collect_doubao_sources(config, playwright_factory=factory)
        navigate.assert_awaited_once()
        page.locator.assert_not_called()
        context.close.assert_awaited_once()
        self.assertEqual(result['failure_stage'], 'navigation')
        self.assertIs(result['question_submission_attempted'], False)
        self.assertIs(result['question_submitted'], False)
        self.assertFalse(result['answer_completed'])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['failure_stage'], 'navigation')
        return result

    async def test_timeout_with_visible_error_records_site_error_and_no_submission(self):
        result = await self.collect_failed_navigation(unavailable=True)
        self.assertEqual(result['failure_reason'], 'website_temporarily_unavailable')
        self.assertEqual(result['collection_status'], 'need_action')

    async def test_timeout_without_site_error_keeps_browser_reason_and_no_submission(self):
        result = await self.collect_failed_navigation(unavailable=False)
        self.assertEqual(result['failure_reason'], 'browser_error:TimeoutError')
        self.assertEqual(result['collection_status'], 'failed')


class IncompleteNativeRunTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def run_failure(self, *, reason='website_temporarily_unavailable', attempted=False, submitted=False,
                    collection_status='need_action', include_flags=True):
        questions = []
        async def collect(config):
            questions.append(config.question)
            value = answer(config.question, response='', answer_completed=False, collection_status=collection_status,
                failure_reason=reason, failure_stage='navigation', question_submission_attempted=attempted,
                question_submitted=submitted)
            if not include_flags:
                value.pop('question_submission_attempted')
                value.pop('question_submitted')
            evidence = config.output_root/'result.json'
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps(value), encoding='utf-8')
            return value, evidence
        async def bridge(value, config, **kwargs):
            return await run_test_plan(value, config, collect=collect, **kwargs)
        with patch('services.desktop_geo_jobs.run_test_plan', bridge):
            self.jobs.start_run(plan(), {'client_mode': 'consumer'})
            self.jobs.worker.join(5)
        self.assertFalse(self.jobs.worker.is_alive())
        self.assertEqual(len(questions), 1)
        return self.jobs.get_task()

    def test_page_unavailable_is_need_action_with_precise_unsent_message_and_evidence(self):
        value = self.run_failure()
        self.assertEqual(value['status'], 'need_action')
        self.assertEqual(value['result']['report']['status'], 'stopped')
        self.assertIn('该页面暂时不可用', value['message'])
        self.assertIn('问题尚未发送', value['message'])
        self.assertEqual(value['recovery']['stage'], 'before_submission')
        saved = self.jobs.store.read(value['id'])
        self.assertEqual(saved['status'], 'need_action')
        self.assertFalse(saved['waiting_for_user'])

    def test_navigation_browser_failure_is_failed_and_known_not_sent(self):
        value = self.run_failure(reason='browser_error:TimeoutError', collection_status='failed')
        self.assertEqual(value['status'], 'failed')
        self.assertIn('页面未能正常打开', value['message'])
        self.assertIn('问题尚未发送', value['message'])
        self.assertEqual(value['recovery']['stage'], 'before_submission')

    def test_unknown_flags_or_attempted_request_never_claim_not_sent(self):
        for options in ({'include_flags': False}, {'attempted': True}, {'attempted': True, 'submitted': True}):
            with self.subTest(options=options):
                value = self.run_failure(**options)
                self.assertNotIn('问题尚未发送', value['message'])
                self.assertNotEqual(value['recovery']['stage'], 'before_submission')

    def test_collector_exception_is_needs_review_and_never_claims_no_submission(self):
        async def collect(_config):
            raise TimeoutError('synthetic interrupted request with unknown submission')
        async def bridge(value, config, **kwargs):
            return await run_test_plan(value, config, collect=collect, **kwargs)
        with patch('services.desktop_geo_jobs.run_test_plan', bridge):
            self.jobs.start_run(plan(), {})
            self.jobs.worker.join(5)
        value = self.jobs.get_task()
        self.assertEqual(value['status'], 'needs_review')
        self.assertEqual(value['recovery']['stage'], 'unknown')
        self.assertNotIn('问题尚未发送', value['message'])

    def test_second_case_exception_does_not_inherit_first_case_submission_state(self):
        calls = []
        async def collect(config, *, progress):
            calls.append(config.question)
            if len(calls) == 2:
                raise TimeoutError('synthetic unknown second request')
            progress({'event': 'question_submission_started', 'question_submission_attempted': True,
                      'question_submitted': False})
            return answer(config.question), config.output_root/'result.json'
        async def no_wait(_duration):
            pass
        async def bridge(value, config, **kwargs):
            return await run_test_plan(value, config, collect=collect, sleep=no_wait, **kwargs)
        with patch('services.desktop_geo_jobs.run_test_plan', bridge):
            self.jobs.start_run(plan(), {})
            self.jobs.worker.join(5)
        value = self.jobs.get_task()
        self.assertEqual(value['status'], 'needs_review')
        self.assertEqual(value['recovery']['stage'], 'unknown')
        self.assertIsNone(value['recovery']['question_submission_attempted'])
        self.assertEqual(value['progress']['case_id'], 'q02')

    def test_report_outcome_uses_failure_which_stopped_attempt_not_earlier_failed_case(self):
        self.jobs.job = {'id': 'synthetic-resumed-outcome', 'kind': 'run', 'status': 'running'}
        self.jobs._apply_incomplete_run({'status': 'stopped', 'stop_reason': 'website_temporarily_unavailable',
            'cases': [
                {'collection_status': 'failed', 'failure_reason': 'answer_timeout',
                 'question_submission_attempted': True, 'question_submitted': True},
                {'collection_status': 'need_action', 'failure_reason': 'website_temporarily_unavailable',
                 'failure_stage': 'navigation', 'question_submission_attempted': False, 'question_submitted': False}]})
        self.assertEqual(self.jobs.job['status'], 'need_action')
        self.assertIs(self.jobs.job['submission_state']['question_submission_attempted'], False)
        self.assertIn('问题尚未发送', self.jobs.job['message'])


class ReportSubmissionProjectionTests(unittest.TestCase):
    def report(self, **fields):
        value = plan()
        state = {'run_id': 'synthetic-projection', 'status': 'stopped',
            'cases': {case['id']: {'status': 'pending'} for case in value['cases']}}
        first = value['cases'][0]
        state['cases'][first['id']] = {'status': 'failed', 'result': answer(first['question'],
            response='', answer_completed=False, collection_status='failed', **fields)}
        return build_report(value, state), state

    def test_only_typed_evidence_is_projected_and_state_is_not_rewritten(self):
        report, state = self.report(question_submission_attempted=False, question_submitted=False, failure_stage='navigation')
        before = copy.deepcopy(state)
        self.assertIs(report['cases'][0]['question_submission_attempted'], False)
        self.assertIs(report['cases'][0]['question_submitted'], False)
        self.assertEqual(report['cases'][0]['failure_stage'], 'navigation')
        self.assertEqual(state, before)
        for row in report['cases'][1:]:
            self.assertIsNone(row['question_submission_attempted'])
            self.assertIsNone(row['question_submitted'])
            self.assertIsNone(row['failure_stage'])

    def test_untyped_submission_or_invalid_stage_remains_unknown(self):
        report, _ = self.report(question_submission_attempted='false', question_submitted=0,
                                failure_stage='raw path / private')
        row = report['cases'][0]
        self.assertIsNone(row['question_submission_attempted'])
        self.assertIsNone(row['question_submitted'])
        self.assertIsNone(row['failure_stage'])

    def test_recovery_never_derives_unsent_claim_from_reason_or_stage_alone(self):
        for state in ({}, {'attempted': False}, {'submitted': False}, {'attempted': 'false', 'submitted': False}):
            message = recovery_message('website_temporarily_unavailable', failure_stage='navigation', **state)
            self.assertNotIn('问题尚未发送', message)

    def test_login_or_verification_reason_is_not_replaced_by_generic_navigation_failure(self):
        for reason, text in (('login_required', '登录'), ('human_verification_required', '安全验证')):
            message = recovery_message(reason, attempted=False, submitted=False, failure_stage='navigation')
            self.assertIn(text, message)
            self.assertNotIn('页面未能正常打开', message)


if __name__ == '__main__':
    unittest.main()
