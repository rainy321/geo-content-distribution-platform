"""Pure mocks for manual waiting and bound conversation receipts; no browser."""
import asyncio
import json
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.doubao_source_collector import (
    CollectorConfig, ManualWindowClosedError, SubmissionReceipt,
    _wait_for_manual_verification, collect_doubao_sources,
)
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_runner import RunConfig, run_test_plan
from tests.test_geo_tests import answer, plan


LOCAL = 'https://www.doubao.com/chat/local_12345'
CANONICAL = 'https://www.doubao.com/chat/987654'


class FakePage:
    def __init__(self):
        self.main_frame = SimpleNamespace(url=LOCAL)
        self.handlers = {}
        self.closed = False

    def on(self, event, callback):
        self.handlers[event] = callback

    def is_closed(self):
        return self.closed

    def navigate(self, url):
        self.main_frame.url = url
        self.handlers.get('framenavigated', lambda _: None)(self.main_frame)


def snapshot(**changes):
    return {'page_url': LOCAL, 'assistant_count': 0, 'user_message_count': 1,
            'question_verified': True, 'answer': '', 'completion_marker': False, 'busy': False,
            'challenge': True, 'client_mode': 'consumer', 'model_label': 'synthetic-model',
            'login_state': 'unknown', **changes}


class ReceiptTests(unittest.TestCase):
    def receipt(self, baseline=0):
        page = FakePage()
        receipt = SubmissionReceipt(page, 'exact synthetic question', baseline)
        receipt.attempted()
        self.assertTrue(receipt.observe(page, 'exact synthetic question', snapshot())[0])
        receipt.manual_action_seen = True
        return page, receipt

    def test_exact_receipt_survives_only_bound_local_to_canonical_transition(self):
        page, receipt = self.receipt()
        page.navigate(CANONICAL)
        after = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0, assistant_count=1)
        verified, method = receipt.observe(page, 'exact synthetic question', after)
        self.assertTrue(verified)
        self.assertEqual(method, 'bound_local_to_canonical_after_manual')
        self.assertFalse(after['question_verified'])
        self.assertEqual(receipt.evidence()['navigation_urls'], [LOCAL, CANONICAL])

    def test_other_routes_new_questions_pages_and_multiple_submits_never_reuse_receipt(self):
        for change in ('external', 'second-conversation', 'different-question', 'different-page', 'new-user', 'resubmit'):
            page, receipt = self.receipt()
            if change == 'external':
                page.navigate('https://other.example/chat/987654')
            page.navigate(CANONICAL)
            if change == 'second-conversation':
                page.navigate('https://www.doubao.com/chat/888888')
                page.navigate(CANONICAL)
            after = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0, assistant_count=1)
            if change == 'new-user':
                after['user_message_count'] = 1
            if change == 'resubmit':
                receipt.attempted()
            with self.subTest(change=change):
                self.assertFalse(receipt.observe(FakePage() if change == 'different-page' else page,
                    'changed question' if change == 'different-question' else 'exact synthetic question', after)[0])

    def test_old_conversation_and_absent_manual_event_are_not_fallback_proof(self):
        page, receipt = self.receipt(baseline=1)
        page.navigate(CANONICAL)
        after = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0, assistant_count=1)
        self.assertFalse(receipt.observe(page, receipt.question, after)[0])

    def test_a_manual_challenge_without_prior_literal_receipt_is_not_submission_proof(self):
        page = FakePage()
        receipt = SubmissionReceipt(page, 'exact synthetic question', 0)
        receipt.attempted()
        receipt.manual_action_seen = True
        page.navigate(CANONICAL)
        after = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0, assistant_count=1)
        self.assertFalse(receipt.observe(page, receipt.question, after)[0])
        self.assertIsNone(receipt.evidence()['observed_url'])
        page, receipt = self.receipt()
        receipt.manual_action_seen = False
        page.navigate(CANONICAL)
        self.assertFalse(receipt.observe(page, receipt.question, after)[0])

    def test_one_half_rendered_user_can_later_establish_exact_literal_receipt(self):
        page = FakePage()
        receipt = SubmissionReceipt(page, 'exact synthetic question', 0)
        receipt.attempted()
        self.assertFalse(receipt.observe(page, receipt.question, snapshot(question_verified=False))[0])
        self.assertIsNone(receipt.invalid_reason)
        self.assertIsNone(receipt.observed_url)
        self.assertTrue(receipt.observe(page, receipt.question, snapshot())[0])
        self.assertEqual(receipt.observed_url, LOCAL)
        self.assertTrue(receipt.observe(page, receipt.question, snapshot(assistant_count=1))[0])
        self.assertFalse(receipt.observe(page, receipt.question, snapshot(question_verified=False))[0])
        self.assertEqual(receipt.invalid_reason, 'different_user_message')
        # A later DOM reappearance cannot clear an observed changed request.
        self.assertFalse(receipt.observe(page, receipt.question, snapshot())[0])

    def test_multiple_users_permanently_block_even_before_exact_literal_or_with_matching_last_user(self):
        for initially_verified in (False, True):
            page = FakePage()
            receipt = SubmissionReceipt(page, 'exact synthetic question', 0)
            receipt.attempted()
            self.assertFalse(receipt.observe(page, receipt.question,
                snapshot(question_verified=initially_verified, user_message_count=2))[0])
            self.assertEqual(receipt.invalid_reason, 'multiple_user_messages')
            self.assertFalse(receipt.observe(page, receipt.question, snapshot())[0])


class ManualWaitingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.page = FakePage()
        self.events = []
        self.result = {'question_submission_attempted': True, 'question_submitted': True,
                       'manual_verification': {'events': [], 'waited_seconds': 0}}

    async def wait(self, config):
        return await _wait_for_manual_verification(self.page, config, self.root, self.result,
                                                   snapshot(), progress=self.events.append)

    async def test_desktop_unlimited_wait_does_not_expire_after_600_seconds(self):
        clock = [0]
        async def sleep(_):
            clock[0] += 700
        with patch('services.doubao_source_collector.time', SimpleNamespace(monotonic=lambda: clock[0])), \
             patch('services.doubao_source_collector.asyncio.sleep', sleep), \
             patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock), \
             patch('services.doubao_source_collector.read_snapshot', new_callable=AsyncMock) as read:
            read.side_effect = [snapshot(), snapshot(challenge=False)]
            final = await self.wait(CollectorConfig(headed=True, manual_wait_seconds=None))
        self.assertFalse(final['challenge'])
        self.assertEqual(self.result['manual_verification']['waited_seconds'], 1400)
        self.assertEqual([event['event'] for event in self.events], ['manual_action_required', 'manual_action_cleared'])

    async def test_cli_finite_wait_keeps_its_budget(self):
        clock = [0]
        async def sleep(_):
            clock[0] += 3
        with patch('services.doubao_source_collector.time', SimpleNamespace(monotonic=lambda: clock[0])), \
             patch('services.doubao_source_collector.asyncio.sleep', sleep), \
             patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock), \
             patch('services.doubao_source_collector.read_snapshot', new_callable=AsyncMock, return_value=snapshot()):
            final = await self.wait(CollectorConfig(headed=True, manual_wait_seconds=2))
        self.assertTrue(final['challenge'])
        self.assertEqual(self.result['manual_verification']['events'][0]['exit_reason'], 'wait_budget_exhausted')

    async def test_unlimited_wait_can_be_cancelled_and_records_exit(self):
        with patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock):
            task = asyncio.create_task(self.wait(CollectorConfig(headed=True, manual_wait_seconds=None)))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.result['manual_verification']['events'][0]['exit_reason'], 'cancelled')

    async def test_closed_window_ends_unlimited_wait_without_loop_or_resubmission(self):
        async def close(_):
            self.page.closed = True
        with patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock), \
             patch('services.doubao_source_collector.asyncio.sleep', close):
            with self.assertRaises(ManualWindowClosedError):
                await self.wait(CollectorConfig(headed=True, manual_wait_seconds=None))
        self.assertEqual(self.result['manual_verification']['events'][0]['exit_reason'], 'window_closed')

    async def test_receipt_is_saved_before_verification_can_remove_user_dom(self):
        page = self.page
        page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
        page.get_by_role = Mock()
        page.evaluate = AsyncMock(return_value='Synthetic browser')
        editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(), evaluate=AsyncMock(return_value={
            'readback':'exact synthetic question', 'method':'contenteditable_inner_text_exact',
            'editor_dom':'<div contenteditable="true">exact synthetic question</div>',
            'inner_text':'exact synthetic question', 'text_content':'exact synthetic question'}))
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        calls = [0]
        async def read(*_args, **_kwargs):
            calls[0] += 1
            if calls[0] <= 2:
                return snapshot(challenge=False, question_verified=False, user_message_count=0)
            if calls[0] == 3:
                # Real consumer/work React can mount one partial user bubble
                # before its exact text is observable; no anchor exists yet.
                return snapshot(challenge=False, question_verified=False, user_message_count=1)
            if calls[0] == 4:
                return snapshot()
            self.assertEqual(len(list(self.root.rglob('submission-observed.json'))), 1)
            page.navigate(CANONICAL)
            return snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0,
                            assistant_count=1, challenge=False, answer='Complete synthetic answer', completion_marker=True)
        clock = [0]
        def monotonic():
            clock[0] += 0.2
            return clock[0]
        with patch('services.doubao_source_collector.launch_context', new_callable=AsyncMock, return_value=context), \
             patch('services.doubao_source_collector._navigate_to_doubao', new_callable=AsyncMock), \
             patch('services.doubao_source_collector._first_visible', new_callable=AsyncMock, side_effect=[None, editor]), \
             patch('services.doubao_source_collector.read_snapshot', read), \
             patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock), \
             patch('services.doubao_source_collector._expand_sources', new_callable=AsyncMock, return_value=([], [], False)) as expand, \
             patch('services.doubao_source_collector.time', SimpleNamespace(monotonic=monotonic)), \
             patch('services.doubao_source_collector.asyncio.sleep', new_callable=AsyncMock):
            config = CollectorConfig(question='exact synthetic question', headed=True, manual_wait_seconds=None,
                                     output_root=self.root/'evidence', profile_dir=self.root/'profile', stable_seconds=.1)
            result, path = await collect_doubao_sources(config, playwright_factory=factory, progress=self.events.append)
        self.assertTrue(result['answer_completed'], result)
        self.assertTrue(result['question_submitted'])
        self.assertEqual(result['question_verification_method'], 'bound_local_to_canonical_after_manual')
        self.assertEqual(editor.press.await_count, 1)
        self.assertEqual(expand.await_args.kwargs['progress'], self.events.append)
        self.assertTrue(next(event for event in self.events if event['event']=='manual_action_required')['question_submitted'])
        self.assertTrue(path.is_file())

    async def test_runner_forwards_manual_phase_with_case_identity(self):
        async def collect(config, *, progress):
            progress({'event':'manual_action_required','reason':'login_required'})
            return answer(config.question), self.root/'synthetic.json'
        value = await run_test_plan(plan(), RunConfig(self.root/'run', CollectorConfig()),
                                    collect=collect, progress=self.events.append, sleep=AsyncMock())
        self.assertEqual(value['status'], 'completed')
        phases = [event for event in self.events if event['event']=='manual_action_required']
        self.assertEqual([event['case_id'] for event in phases], ['q01','q02','q03','q04'])


class DesktopProgressTests(unittest.TestCase):
    def test_source_counts_are_durable_bounded_and_do_not_copy_urls_or_dom(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=DesktopGeoJobs(Path(directory))
            self.addCleanup(jobs.shutdown)
            jobs.job={'id':'synthetic-source-count','status':'running'}
            jobs._collector_progress({'event':'source_progress','phase':'inline_citations','processed':7,'total':28,'case_id':'q01',
                                      'url':'https://private.example/token','dom':'<private-dom>','message':'untrusted display'})
            saved=jobs.store.read(jobs.job['id'])
            self.assertEqual(saved['progress'],{'event':'source_progress','phase':'inline_citations','processed':7,'total':28,'case_id':'q01'})
            self.assertIn('已检查 7/28',saved['message'])
            self.assertNotIn('private',json.dumps(saved))
            self.assertNotIn('untrusted display',json.dumps(saved))
            for extra in ({'phase':'arbitrary-dom'},{'processed':True},{'total':'28'},{'total':31},{'processed':29},{'processed':-1}):
                jobs._collector_progress({'event':'source_progress','phase':'inline_citations','processed':7,'total':28,**extra})
                self.assertEqual(jobs.store.read(jobs.job['id']),saved)
            jobs._collector_progress({'event':'source_progress','phase':'inline_citations','processed':8,'total':28,
                                      'case_id':'https://private.example/raw'})
            sanitized=jobs.store.read(jobs.job['id'])
            self.assertNotIn('case_id',sanitized['progress'])
            self.assertNotIn('private.example',json.dumps(sanitized))

    def test_source_extraction_phase_is_durable_and_uses_controlled_message(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = DesktopGeoJobs(Path(directory))
            self.addCleanup(jobs.shutdown)
            jobs.job = {'id': 'synthetic-extract-phase', 'status': 'running'}
            jobs._collector_progress({'event': 'answer_completed', 'state': 'extracting_sources',
                'case_id': 'q01', 'question_submitted': True, 'message': 'untrusted source text'})
            saved = jobs.store.read(jobs.job['id'])
            self.assertEqual(saved['progress']['event'], 'answer_completed')
            self.assertIn('回答已完成，正在检查可见引用和网址', saved['message'])
            self.assertFalse(saved['waiting_for_user'])
            self.assertNotIn('untrusted source text', json.dumps(saved))

    def test_progress_is_safe_durable_and_wait_is_cancellable_without_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = DesktopGeoJobs(Path(directory))
            self.addCleanup(jobs.shutdown)
            self.assertIsNone(jobs._config({}).manual_wait_seconds)
            self.assertNotIn('progress', asdict(jobs._config({})))
            entered = threading.Event()
            async def operation():
                jobs._collector_progress({'event':'manual_action_required','reason':'human_verification_required',
                    'question_submitted':True,'api_key':'synthetic-must-not-save','message':'untrusted message'})
                entered.set()
                await asyncio.Event().wait()
            jobs._start('synthetic-test', operation)
            self.assertTrue(entered.wait(timeout=3))
            current = jobs.get_task()
            self.assertEqual(current['status'], 'running')
            self.assertTrue(current['waiting_for_user'])
            self.assertEqual(jobs.store.read(current['id'])['wait_reason'], 'human_verification_required')
            self.assertNotIn('synthetic-must-not-save', json.dumps(current))
            self.assertNotIn('untrusted message', json.dumps(current))
            jobs.stop_task()
            jobs.worker.join(timeout=3)
            self.assertEqual(jobs.get_task()['status'], 'stopped')
            self.assertFalse(jobs.get_task()['waiting_for_user'])


if __name__ == '__main__':
    unittest.main()
