"""Before-Enter durability checks with synthetic page methods, no browser."""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services import doubao_source_collector as collector
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_doubao_manual_wait import FakePage, snapshot


class SubmissionCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.page = FakePage()
        self.page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
        self.page.get_by_role = Mock()
        self.page.evaluate = AsyncMock(return_value='synthetic browser')
        self.editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(side_effect=RuntimeError('synthetic stop after press')),
            evaluate=AsyncMock(return_value={'readback':'exact synthetic question','method':'contenteditable_inner_text_exact',
                'editor_dom':'<div>exact synthetic question</div>', 'inner_text':'exact synthetic question',
                'text_content':'exact synthetic question'}))

    async def collect(self, *, progress=None, writer=None):
        context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        configuration = collector.CollectorConfig(question='exact synthetic question', output_root=self.root/'geo-content/evidence',
            profile_dir=self.root/'profile', stable_seconds=.1, timeout_seconds=1)
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', AsyncMock()), \
             patch.object(collector, '_first_visible', AsyncMock(side_effect=[None,self.editor])), \
             patch.object(collector, 'read_snapshot', AsyncMock(return_value=snapshot(challenge=False,
                 question_verified=False, user_message_count=0))), \
             patch.object(collector, '_save_checkpoint', AsyncMock()):
            if writer:
                with patch.object(collector, 'write_evidence_result', side_effect=writer):
                    return await collector.collect_doubao_sources(configuration, playwright_factory=factory, progress=progress)
            return await collector.collect_doubao_sources(configuration, playwright_factory=factory, progress=progress)

    async def test_both_collector_and_native_attempt_are_on_disk_before_press(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        jobs.job = {'id':'synthetic-checkpoint', 'kind':'draft', 'status':'running'}
        observed = []
        def progress(event):
            if event['event'] == 'question_submission_started':
                self.editor.press.assert_not_awaited()
                value = json.loads(Path(event['evidence_path']).read_text(encoding='utf-8'))
                self.assertTrue(value['question_submission_attempted'])
                self.assertNotIn('finished_at', value)
                jobs._collector_progress(event)
                stored = jobs.store.read(jobs.job['id'])
                self.assertTrue(stored['submission_state']['question_submission_attempted'])
                observed.append(stored['evidence_path'])
        async def press(_key):
            self.assertEqual(len(observed), 1)
            raise RuntimeError('synthetic end after possible send')
        self.editor.press.side_effect = press
        result, _ = await self.collect(progress=progress)
        self.assertEqual(self.editor.press.await_count, 1)
        self.assertTrue(result['question_submission_attempted'])

    async def test_collector_checkpoint_failure_prevents_enter(self):
        original = collector.write_evidence_result
        calls = []
        def writer(path, result):
            calls.append(result.get('failure_stage'))
            if len(calls) == 1:
                raise OSError('synthetic checkpoint storage error')
            return original(path, result)
        result, path = await self.collect(writer=writer)
        self.editor.press.assert_not_awaited()
        self.assertEqual(result['failure_stage'], 'submission_checkpoint')
        self.assertFalse(result['question_submitted'])
        self.assertTrue(path.is_file())

    async def test_missing_receipt_after_one_send_stays_unknown_and_needs_review(self):
        self.editor.press.side_effect = None
        result, path = await self.collect()
        self.assertEqual(self.editor.press.await_count, 1)
        self.assertTrue(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertFalse(result['answer_completed'])
        self.assertEqual(result['failure_reason'], 'submission_receipt_timeout')
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertTrue(path.is_file())
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(saved['question_submission_attempted'])
        self.assertFalse(saved['question_submitted'])

    async def test_native_checkpoint_failure_prevents_enter_and_never_retries(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        jobs.job = {'id':'synthetic-native-failure', 'kind':'draft', 'status':'running'}
        def progress(event):
            if event['event'] == 'question_submission_started':
                with patch.object(jobs.store, 'save', side_effect=OSError('synthetic native disk error')):
                    jobs._collector_progress(event)
        result, path = await self.collect(progress=progress)
        self.editor.press.assert_not_awaited()
        self.assertEqual(result['failure_stage'], 'submission_checkpoint')
        self.assertTrue(result['question_submission_attempted'])
        self.assertTrue(path.is_file())


if __name__ == '__main__':
    unittest.main()
