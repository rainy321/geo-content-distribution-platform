"""Internal AI tasks preserve literal JSON; GEO test questions remain plain."""
import unittest
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.doubao_submission_transport import LITERAL_PREAMBLE, submission_text
from services.doubao_source_collector import CollectorConfig, SubmissionReceipt, collect_doubao_sources
from tests.test_doubao_manual_wait import FakePage, snapshot


class LiteralTransportTests(unittest.TestCase):
    def test_plain_questions_are_identical_and_nested_fences_cannot_close_envelope(self):
        question = '客户资料：{"quoted": "\\\"原文\\\"", "path": "C:\\\\data"}\n```json\n{}\n```'
        self.assertEqual(submission_text(question), question)
        sent = submission_text(question, 'verbatim_fenced')
        self.assertEqual(sent, LITERAL_PREAMBLE+'\n````text\n'+question+'\n````')
        with self.assertRaises(ValueError):
            CollectorConfig(submission_format='semantic_normalization')

    def test_fenced_dom_needs_exact_editor_readback_one_send_and_fresh_user_context(self):
        question = '{"quoted":"\\\"literal\\\""}'
        sent = submission_text(question, 'verbatim_fenced')
        for mode in ('valid', 'no-editor-proof', 'wrong-editor', 'old-user', 'two-users', 'second-enter', 'plain-literal-bypass'):
            page = FakePage()
            receipt = SubmissionReceipt(page, question, 0, submitted_text=sent,
                editor_readback=None if mode=='no-editor-proof' else 'changed' if mode=='wrong-editor' else sent,
                submission_format='verbatim_fenced', baseline_user_count=1 if mode=='old-user' else 0)
            receipt.attempted()
            if mode=='second-enter':
                receipt.attempted()
            state = snapshot(question_verified=mode=='plain-literal-bypass',
                verbatim_question_verified=mode!='plain-literal-bypass', user_message_count=2 if mode=='two-users' else 1)
            with self.subTest(mode=mode):
                verified, method = receipt.observe(page, question, state)
                self.assertEqual(verified,mode=='valid')
                if verified:
                    self.assertEqual(method,'verbatim_fenced_dom_with_editor_readback')


class PreSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_reuse_requires_current_and_rechecked_label_and_unknown_options_stay_blocked(self):
        for problem in ('unknown-label', 'different-label', 'stale-label'):
            page = FakePage()
            control = SimpleNamespace(wait_for=AsyncMock(), click=AsyncMock())
            page.locator = Mock(return_value=SimpleNamespace(first=control))
            menu = SimpleNamespace(filter=Mock(return_value=SimpleNamespace(count=AsyncMock(return_value=0))))
            page.get_by_role = Mock(return_value=menu)
            page.get_by_text = Mock()
            page.evaluate = AsyncMock(return_value='Synthetic browser')
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), browser=None, close=AsyncMock())
            @asynccontextmanager
            async def factory():
                yield SimpleNamespace()
            initial = snapshot(challenge=False, question_verified=False, user_message_count=0,
                model_label=None if problem == 'unknown-label' else 'other model' if problem == 'different-label' else '豆包2.1 Lite\n高')
            changed = {**initial, 'model_label': 'different model'}
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory, \
                 patch('services.doubao_source_collector.launch_context', new_callable=AsyncMock, return_value=context), \
                 patch('services.doubao_source_collector._navigate_to_doubao', new_callable=AsyncMock), \
                 patch('services.doubao_source_collector._first_visible', new_callable=AsyncMock, return_value=None), \
                 patch('services.doubao_source_collector.read_snapshot', new_callable=AsyncMock,
                       side_effect=[initial, initial, changed]), \
                 patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock):
                config = CollectorConfig(question='Synthetic task', requested_model_label='豆包2.1 Lite',
                    output_root=Path(directory)/'evidence', profile_dir=Path(directory)/'profile')
                result, _ = await collect_doubao_sources(config, playwright_factory=factory)
                self.assertFalse(result['question_submission_attempted'])
                self.assertEqual(result['failure_reason'], 'model_selection_not_confirmed' if problem == 'stale-label'
                    else 'model_option_not_uniquely_available')
                self.assertEqual(control.click.await_count, 0 if problem == 'stale-label' else 1)

    async def test_editor_mutation_and_old_user_context_do_not_press_enter(self):
        question = '任务：返回JSON。\n{"quoted":"\\\"literal\\\""}'
        sent = submission_text(question, 'verbatim_fenced')
        for problem in ('changed-editor', 'existing-user'):
            page = FakePage()
            page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
            page.get_by_role = Mock()
            page.evaluate = AsyncMock(return_value='Synthetic browser')
            readback = sent.replace('literal', 'changed') if problem == 'changed-editor' else sent
            editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(), evaluate=AsyncMock(return_value={
                'readback':readback,'method':'contenteditable_inner_text_exact','editor_dom':'<div contenteditable="true"></div>',
                'inner_text':readback,'text_content':readback}))
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), browser=None, close=AsyncMock())
            @asynccontextmanager
            async def factory():
                yield SimpleNamespace()
            initial = snapshot(challenge=False, question_verified=False,
                               user_message_count=1 if problem == 'existing-user' else 0)
            events = []
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory, \
                 patch('services.doubao_source_collector.launch_context', new_callable=AsyncMock, return_value=context), \
                 patch('services.doubao_source_collector._navigate_to_doubao', new_callable=AsyncMock), \
                 patch('services.doubao_source_collector._first_visible', new_callable=AsyncMock, side_effect=[None, editor]), \
                 patch('services.doubao_source_collector.read_snapshot', new_callable=AsyncMock, return_value=initial), \
                 patch('services.doubao_source_collector._save_checkpoint', new_callable=AsyncMock):
                config = CollectorConfig(question=question, submission_format='verbatim_fenced',
                    output_root=Path(directory)/'evidence', profile_dir=Path(directory)/'profile')
                result, path = await collect_doubao_sources(config, playwright_factory=factory, progress=events.append)
                editor.press.assert_not_awaited()
                self.assertFalse(result['question_submission_attempted'])
                self.assertFalse(result['question_submitted'])
                self.assertEqual(result['collection_status'], 'need_action', result)
                self.assertEqual(result['failure_reason'], 'editor_readback_mismatch_not_submitted' if problem == 'changed-editor'
                    else 'submission_fresh_user_context_not_confirmed')
                self.assertFalse(any(event['event']=='question_sent' for event in events))
                proof = json.loads((path.parent/'editor-readback.json').read_text(encoding='utf-8'))
                self.assertEqual(proof['editor_readback'], readback)
                self.assertEqual(proof['verified'], problem == 'existing-user')
                self.assertFalse((path.parent/'submission-observed.json').exists())


if __name__ == '__main__':
    unittest.main()
