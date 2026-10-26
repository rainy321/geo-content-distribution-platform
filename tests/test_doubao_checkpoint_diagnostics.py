"""Screenshot failures after a single submission, using local mocks only."""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services import doubao_source_collector as collector
from tests.test_doubao_manual_wait import FakePage, snapshot


NOTICE = '已为您启动调研任务，正在梳理候选方案。调研完成后会以飞书文档形式交付给您。您可以继续发送其他任务，无需等待。'


class CheckpointDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.page = FakePage()
        self.config = collector.CollectorConfig()
        self.result = {'question_submission_attempted': True, 'question_submitted': True,
                       'answer_completed': True, 'screenshots': []}

    async def test_answer_dom_is_saved_before_optional_screenshot_timeout(self):
        evidence = snapshot(challenge=False, answer='完整的已核验回答')
        async def screenshot(**kwargs):
            self.assertEqual(json.loads((self.root/'answer.json').read_text(encoding='utf-8')), evidence)
            self.assertEqual(kwargs['timeout'], 5000)
            raise TimeoutError('PRIVATE_BROWSER_PATH_OR_CREDENTIAL')
        self.page.screenshot = AsyncMock(side_effect=screenshot)
        with patch.object(collector, 'read_snapshot', AsyncMock(return_value=evidence)):
            await collector._save_checkpoint(self.page, self.config, self.root, self.result, 'answer')
        self.assertTrue(self.result['answer_completed'])
        self.assertEqual(self.result['screenshots'], [])
        diagnostic = self.result['diagnostic_errors'][0]
        self.assertEqual(diagnostic['checkpoint'], 'answer')
        self.assertEqual(diagnostic['operation'], 'screenshot')
        self.assertEqual(diagnostic['error_type'], 'TimeoutError')
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))
        self.assertEqual(json.loads((self.root/'answer-diagnostic.json').read_text(encoding='utf-8')), diagnostic)
        self.page.screenshot.assert_awaited_once()

    async def test_pre_submission_and_unexpected_screenshot_errors_remain_strict(self):
        for attempted, name, error in ((False, 'question', TimeoutError('strict pre-send')),
                                       (True, 'answer', OSError('strict storage failure'))):
            with self.subTest(attempted=attempted, name=name):
                self.result['question_submission_attempted'] = attempted
                self.page.screenshot = AsyncMock(side_effect=error)
                with patch.object(collector, 'read_snapshot', AsyncMock(return_value=snapshot())):
                    with self.assertRaises(type(error)):
                        await collector._save_checkpoint(self.page, self.config, self.root, self.result, name)
        self.assertNotIn('diagnostic_errors', self.result)

    async def test_unknown_dom_cannot_become_a_completed_answer_or_screenshot_only_success(self):
        self.page.screenshot = AsyncMock()
        with patch.object(collector, 'read_snapshot', AsyncMock(side_effect=TimeoutError('unknown DOM'))):
            with self.assertRaises(TimeoutError):
                await collector._save_checkpoint(self.page, self.config, self.root, self.result, 'answer')
        self.assertFalse(self.result['answer_completed'])
        self.page.screenshot.assert_not_awaited()
        self.assertNotIn('diagnostic_errors', self.result)

    async def test_manual_verification_keeps_polling_same_window_after_diagnostic_timeout(self):
        self.page.screenshot = AsyncMock(side_effect=TimeoutError('screenshot unavailable'))
        self.result['manual_verification'] = {'events': [], 'waited_seconds': 0}
        events, clock = [], [0]
        async def sleep(_):
            self.assertFalse(self.page.is_closed())
            clock[0] += 700
        with patch.object(collector, 'read_snapshot', AsyncMock(side_effect=[snapshot(), snapshot(), snapshot(challenge=False)])), \
                patch.object(collector, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(collector.asyncio, 'sleep', sleep):
            final = await collector._wait_for_manual_verification(self.page,
                collector.CollectorConfig(headed=True, manual_wait_seconds=None), self.root,
                self.result, snapshot(), progress=events.append)
        self.assertFalse(final['challenge'])
        self.assertFalse(self.page.is_closed())
        self.assertEqual([x['event'] for x in events], ['manual_action_required', 'manual_action_cleared'])
        self.assertEqual(self.result['manual_verification']['waited_seconds'], 1400)
        self.assertEqual(self.result['diagnostic_errors'][0]['checkpoint'], 'manual-action-1')


class SingleSubmissionDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, *, deferred=False, pre_send_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = FakePage()
            page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
            page.get_by_role = Mock()
            page.evaluate = AsyncMock(return_value='Synthetic browser')
            sent, polls, clock = [False], [0], [0]
            async def press(_):
                sent[0] = True
            editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(side_effect=press),
                evaluate=AsyncMock(return_value={'readback': 'exact synthetic question',
                    'method': 'contenteditable_inner_text_exact', 'editor_dom': '<div>exact synthetic question</div>',
                    'inner_text': 'exact synthetic question', 'text_content': 'exact synthetic question'}))
            async def read(*_args, **_kwargs):
                if not sent[0]:
                    return snapshot(challenge=False, question_verified=False, user_message_count=0)
                polls[0] += 1
                return snapshot(challenge=polls[0] <= 2, assistant_count=1,
                    answer=NOTICE if deferred else '已经核验的完整回答，包含实际结论。', completion_marker=True)
            async def screenshot(**kwargs):
                name = Path(kwargs['path']).stem
                if sent[0] or pre_send_failure and name == 'question':
                    raise TimeoutError('PRIVATE screenshot detail')
                Path(kwargs['path']).write_bytes(b'synthetic evidence')
            page.screenshot = AsyncMock(side_effect=screenshot)
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), browser=None, close=AsyncMock())
            @asynccontextmanager
            async def factory():
                yield SimpleNamespace()
            async def sleep(_):
                clock[0] += .5
            events = []
            def progress(event):
                if event['event'] in {'manual_action_required', 'manual_action_cleared', 'background_task_waiting'}:
                    context.close.assert_not_awaited()
                events.append(event)
            expand = AsyncMock(return_value=([], [], False))
            with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
                    patch.object(collector, '_navigate_to_doubao', AsyncMock()), \
                    patch.object(collector, '_first_visible', AsyncMock(side_effect=[None, editor])), \
                    patch.object(collector, 'read_snapshot', read), \
                    patch.object(collector, '_expand_sources', expand), \
                    patch.object(collector, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                    patch.object(collector.asyncio, 'sleep', sleep):
                result, path = await collector.collect_doubao_sources(collector.CollectorConfig(
                    question='exact synthetic question', output_root=root/'evidence', profile_dir=root/'profile',
                    headed=True, manual_wait_seconds=None, timeout_seconds=3, stable_seconds=.1),
                    playwright_factory=factory, progress=progress)
            self.assertTrue(path.is_file())
            self.assertEqual(editor.press.await_count, 0 if pre_send_failure else 1)
            return result, events, expand.await_count

    async def test_manual_and_answer_screenshot_timeouts_preserve_one_sent_answer(self):
        result, events, extractions = await self.collect()
        self.assertEqual(result['collection_status'], 'success')
        self.assertTrue(result['answer_completed'])
        self.assertEqual(extractions, 1)
        self.assertIn('manual_action_cleared', [x['event'] for x in events])
        self.assertEqual({x['checkpoint'] for x in result['diagnostic_errors']}, {'manual-action-1', 'answer'})
        self.assertIsNone(result['failure_reason'])

    async def test_pending_notice_with_screenshot_timeouts_is_not_an_exposure_zero(self):
        result, events, extractions = await self.collect(deferred=True)
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertEqual(result['failure_reason'], 'background_task_timeout')
        self.assertFalse(result['answer_completed'])
        self.assertEqual(extractions, 0)
        self.assertIsNone(result['visible_source_count'])
        self.assertEqual(sum(x['event'] == 'background_task_waiting' for x in events), 1)
        self.assertIn('deferred-notice', {x['checkpoint'] for x in result['diagnostic_errors']})

    async def test_question_screenshot_failure_still_prevents_the_first_send(self):
        result, _, extractions = await self.collect(pre_send_failure=True)
        self.assertFalse(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertEqual(result['failure_stage'], 'question_evidence')
        self.assertEqual(extractions, 0)
