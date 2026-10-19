"""Only a later source sidecar is written; the existing answer is never sent again."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from services.doubao_source_collector import CollectorConfig, reinspect_existing_sources
from services.doubao_answer_identity import prove_progress_body_elision
from tests.test_doubao_manual_wait import CANONICAL, FakePage, snapshot


class ExistingPage(FakePage):
    def remove_listener(self, name, callback):
        if self.handlers.get(name) == callback:
            del self.handlers[name]


class SourceReinspectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original_path = self.root/'original/result.json'
        self.original_path.parent.mkdir()
        self.original = {'schema_version': '1.1', 'run_id': 'original-answer', 'question': 'exact question',
            'response': 'Original complete answer with public sources.', 'question_submitted': True,
            'question_submission_attempted': True, 'answer_completed': True, 'collection_status': 'success',
            'conversation_url': CANONICAL, 'timestamp': '2026-10-19T12:00:00+08:00',
            'environment': {'client_mode': 'consumer', 'model_label': 'original-model'},
            'displayed_sources': [], 'citations': [{'title': 'Source', 'url': None}]}
        self.original_path.write_text(json.dumps(self.original), encoding='utf-8')
        self.original_bytes = self.original_path.read_bytes()
        self.config = CollectorConfig(question='exact question', output_root=self.root/'inspections')
        self.page = ExistingPage()
        self.page.navigate(CANONICAL)
        self.page.goto = Mock(side_effect=AssertionError('must not navigate'))
        self.page.locator = Mock(side_effect=AssertionError('mocked source helper must not edit any input'))
        self.events = []
        self.current = snapshot(page_url=CANONICAL, challenge=False, question_verified=True, assistant_count=1,
            user_message_count=1, completion_marker=True, answer=self.original['response'])
        self.sources = [{'scope': 'inline_citation', 'title': 'Source',
                         'href': 'https://public-source.example/article', 'evidence_file': 'synthetic.json'}]

    async def inspect(self, *, current=None, expand=None):
        read = AsyncMock(return_value=current or self.current)
        expand = expand or AsyncMock(return_value=(self.sources, [], False))
        with patch('services.doubao_source_collector.read_snapshot', read), \
             patch('services.doubao_source_collector._save_checkpoint', AsyncMock()), \
             patch('services.doubao_source_collector._expand_sources', expand):
            result = await reinspect_existing_sources(self.config, self.original_path, page=self.page,
                                                     progress=self.events.append)
        self.page.goto.assert_not_called()
        self.page.locator.assert_not_called()
        self.assertFalse(self.page.closed)
        self.assertNotIn('framenavigated', self.page.handlers)
        return result, expand

    async def test_exact_existing_answer_produces_separate_traceable_source_result(self):
        (result, path), expand = await self.inspect()
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)
        self.assertEqual(result['mode'], 'inspect_existing_answer')
        self.assertEqual(result['collection_status'], 'success')
        self.assertEqual(result['response'], self.original['response'])
        self.assertEqual(result['original_answer_timestamp'], self.original['timestamp'])
        self.assertEqual(result['original_result_sha256'], hashlib.sha256(self.original_bytes).hexdigest())
        self.assertTrue(result['inspection_provenance']['original_result_unchanged'])
        self.assertFalse(result['inspection_provenance']['question_sent'])
        self.assertFalse(result['question_submission_attempted'])
        self.assertEqual(result['citations'][0]['url'], self.sources[0]['href'])
        self.assertTrue(path.is_relative_to(self.root/'inspections'))
        self.assertTrue(path.is_file())
        self.assertEqual(expand.await_count, 1)
        self.assertEqual(expand.await_args.kwargs['progress'],self.events.append)
        self.assertEqual([e['event'] for e in self.events], ['answer_completed'])

    async def test_changed_question_answer_conversation_or_completion_never_extracts(self):
        for change in ({'answer': 'Different answer'}, {'question_verified': False},
                       {'page_url': 'https://www.doubao.com/chat/77777'}, {'user_message_count': 2},
                       {'completion_marker': False}, {'busy': True}, {'challenge': True}, {'client_mode': 'work'}):
            with self.subTest(change=change):
                (result, _), expand = await self.inspect(current={**self.current, **change})
                self.assertEqual(result['collection_status'], 'need_action')
                self.assertFalse(result['answer_completed'])
                self.assertIsNone(result['visible_citation_count'])
                expand.assert_not_awaited()
                self.assertEqual(self.original_path.read_bytes(), self.original_bytes)

    async def test_navigation_away_then_back_during_sources_cannot_rebind(self):
        async def change(*_args, **_kwargs):
            self.page.navigate('https://www.doubao.com/chat/77777')
            self.page.navigate(CANONICAL)
            return self.sources, [], False
        (result, _), _ = await self.inspect(expand=AsyncMock(side_effect=change))
        self.assertEqual(result['failure_reason'], 'inspection_conversation_changed')
        self.assertEqual(result['citations'], [])
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)

    async def test_original_file_changed_by_another_actor_cannot_publish_a_sidecar_claim(self):
        async def change(*_args, **_kwargs):
            changed = {**self.original, 'timestamp': '2026-10-20T12:00:00+08:00'}
            self.original_path.write_text(json.dumps(changed), encoding='utf-8')
            return self.sources, [], False
        (result, _), _ = await self.inspect(expand=AsyncMock(side_effect=change))
        self.assertEqual(result['failure_reason'], 'inspection_original_result_changed')
        self.assertFalse(result['answer_completed'])
        self.assertIsNot(result['inspection_provenance']['original_result_unchanged'], True)

    async def test_cancel_saves_separate_incomplete_inspection_without_closing_main_page(self):
        expand = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.inspect(expand=expand)
        paths = list((self.root/'inspections').glob('inspect-*/result.json'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text())['failure_reason'], 'source_reinspection_cancelled')
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)
        self.assertFalse(self.page.closed)

    async def test_wrong_expected_question_or_output_inside_original_directory_rejected(self):
        for config in (CollectorConfig(question='another question', output_root=self.root/'inspections'),
                       CollectorConfig(question='exact question', output_root=self.original_path.parent)):
            with self.assertRaises(ValueError):
                await reinspect_existing_sources(config, self.original_path, page=self.page)
        self.assertEqual(self.original_path.read_bytes(), self.original_bytes)

    async def test_separate_progress_bodies_may_disappear_only_with_authenticated_exact_dom_proof(self):
        progress = '继续核实其余几家。'
        final = self.original['response']
        body = lambda text: '<div data-testid="message_text_content" data-streaming="false"><div>'+text+'</div></div>'
        original_snapshot = {'answer': progress+'\n\n'+final, 'answer_dom': body(progress)+'\n'+body(final)}
        snapshot_bytes = json.dumps(original_snapshot).encode('utf-8')
        (self.original_path.parent/'answer.json').write_bytes(snapshot_bytes)
        changed = {**self.original, 'response': original_snapshot['answer'],
                   'evidence_manifest': [{'path':'answer.json','sha256':hashlib.sha256(snapshot_bytes).hexdigest()}]}
        self.original_path.write_text(json.dumps(changed), encoding='utf-8')
        original_bytes = self.original_path.read_bytes()
        current = {**self.current, 'answer_dom': body(final)}
        (result, _), expand = await self.inspect(current=current)
        self.assertEqual(result['collection_status'], 'success')
        self.assertEqual(result['response'], changed['response'])
        self.assertEqual(result['original_response_sha256'], hashlib.sha256(changed['response'].encode()).hexdigest())
        self.assertEqual(result['inspection_provenance']['answer_identity_method'], 'exact_final_body_with_dom_proven_progress_prefix')
        self.assertEqual(result['inspection_provenance']['omitted_progress_body_count'], 1)
        self.assertEqual(self.original_path.read_bytes(), original_bytes)
        self.assertEqual(expand.await_count, 1)
        # Merely supplying a DOM file without the raw manifest's exact digest
        # cannot authorize a different current response.
        changed['evidence_manifest'][0]['sha256'] = '0'*64
        self.original_path.write_text(json.dumps(changed), encoding='utf-8')
        (invalid, _), expand = await self.inspect(current=current)
        self.assertEqual(invalid['failure_reason'], 'inspection_answer_changed')
        expand.assert_not_awaited()


class AnswerIdentityTests(unittest.TestCase):
    def test_not_an_arbitrary_suffix_or_semantic_similarity_check(self):
        body = lambda text: '<div data-testid="message_text_content" data-streaming="false"><div>'+text+'</div></div>'
        progress, final = '继续核实候选官网。', 'Final exact answer.'
        old = {'answer': progress+'\n\n'+final, 'answer_dom': body(progress)+body(final)}
        current = {'answer': final, 'answer_dom': body(final)}
        self.assertIsNotNone(prove_progress_body_elision(old['answer'], old, current))
        parallel = {**old, 'answer': '继续并行检索候选官网。\n\n'+final,
                    'answer_dom': body('继续并行检索候选官网。')+body(final)}
        self.assertIsNotNone(prove_progress_body_elision(parallel['answer'], parallel, current))
        for altered_old, altered_current in (
            ({**old, 'answer_dom': body(progress+' '+final)}, current),
            ({**old, 'answer_dom': body('该公司已经通过合规认证。')+body(final)}, current),
            (old, {**current, 'answer': 'Final edited answer.'}),
            (old, {**current, 'answer_dom': body('Changed DOM answer.')}),
            (old, {**current, 'answer_dom': body(final).replace('data-streaming="false"', 'data-streaming="true"')}),
            ({**old, 'answer_dom': body('继续核实<table><tr><td>重要事实</td></tr></table>')+body(final)}, current),
        ):
            self.assertIsNone(prove_progress_body_elision(altered_old['answer'], altered_old, altered_current))


if __name__ == '__main__':
    unittest.main()
