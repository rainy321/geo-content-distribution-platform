"""Source controls must stay bound to one synthetic canonical answer."""
import json
import hashlib
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import doubao_source_collector as collector


URL = 'https://www.doubao.com/chat/123456789'
OTHER = 'https://www.doubao.com/chat/987654321'
QUESTION = '合成来源核验问题'
ANSWER = '合成目标回答。'
HTML = '''<meta charset="utf-8"><title>合成来源边界</title>
<button data-testid="workspace-switch-chip">豆包对话</button>
<button data-testid="chat_input_action_model">快速</button><textarea></textarea>
<div data-message-role="user">合成来源核验问题</div>
<div data-message-role="assistant">
 <div data-testid="message_text_content">合成目标回答。</div>
 <button data-testid="message_action_copy">复制</button>
 <button data-testid="search-references-button" onclick="window.sourceClicks++; document.querySelector('#sources').hidden=false">参考资料</button>
 <div data-testid="search-references" id="sources" hidden><a data-testid="reference-item" href="https://public-source.example/target"><span data-testid="reference-title">合成目标来源</span></a></div>
</div><script>window.sourceClicks=0</script>'''


class SourceContextDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.runtime = await async_playwright().start()
        self.addAsyncCleanup(self.runtime.stop)
        self.browser = await self.runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        await self.page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=HTML))
        await self.page.goto(URL)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True)

    async def mutation(self, script):
        await self.page.locator('[data-testid="search-references-button"]').evaluate(
            '(e, script) => e.addEventListener("click", () => { eval(script) })', script)

    async def test_unverified_initial_context_discards_all_source_markers(self):
        await self.page.evaluate('''() => {
            document.querySelector('[data-message-role="user"]').textContent='另一个合成问题';
            document.querySelector('#sources').hidden=false;
            document.querySelector('[data-testid="reference-item"]').href='https://foreign-source.example/unverified';
        }''')
        items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertEqual(items, [])
        self.assertEqual(shots, [])
        self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
        self.assertFalse(list(self.root.glob('sources-*.json')))
        self.assertFalse(list(self.root.glob('sources-*.png')))

    async def test_source_click_cannot_save_a_different_synthetic_conversation(self):
        await self.mutation('''history.pushState({}, '', '/chat/987654321');
            document.querySelector('[data-message-role="user"]').textContent = '另一个合成问题';
            document.querySelector('[data-testid="message_text_content"]').textContent = '非目标合成答复';
            document.querySelector('[data-testid="reference-item"]').href = 'https://foreign-source.example/not-target';''')
        items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertNotIn('foreign-source.example', json.dumps(items))
        self.assertEqual(shots, [])
        self.assertFalse(list(self.root.glob('sources-*.json')))
        self.assertFalse(list(self.root.glob('sources-*.png')))
        proof = json.loads((self.root/'source-context.json').read_text(encoding='utf8'))
        self.assertTrue(proof['blocked'])
        self.assertEqual(await self.page.evaluate('window.sourceClicks'), 1)

    async def test_navigation_away_and_back_still_invalidates_source_phase(self):
        await self.mutation("history.pushState({}, '', '/chat/987654321'); history.pushState({}, '', '/chat/123456789')")
        items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertEqual(shots, [])
        self.assertFalse(list(self.root.glob('sources-*.json')))

    async def test_same_route_different_question_is_not_a_valid_source_checkpoint(self):
        await self.mutation("document.querySelector('[data-message-role=\"user\"]').textContent='另一个合成问题'")
        _items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertEqual(shots, [])
        self.assertFalse(list(self.root.glob('sources-*.json')))

    async def test_navigation_during_screenshot_discards_in_memory_capture(self):
        screenshot = self.page.screenshot
        async def changed(*args, **kwargs):
            image = await screenshot(*args, **kwargs)
            await self.page.evaluate("history.pushState({}, '', '/chat/987654321')")
            return image
        with patch.object(self.page, 'screenshot', side_effect=changed):
            _items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertEqual(shots, [])
        self.assertFalse(list(self.root.glob('sources-*.png')))
        self.assertFalse(list(self.root.glob('sources-*.json')))

    async def test_unchanged_target_retains_its_own_source_and_checkpoint(self):
        items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertFalse(incomplete)
        self.assertEqual([item['href'] for item in items], ['https://public-source.example/target'])
        self.assertEqual(shots, ['sources-1.png'])
        saved = json.loads((self.root/'sources-1.json').read_text(encoding='utf8'))
        self.assertEqual(saved['page_url'], URL)
        self.assertEqual(saved['answer'], ANSWER)
        self.assertTrue(saved['question_verified'])
        proof = json.loads((self.root/'source-context.json').read_text(encoding='utf8'))
        self.assertFalse(proof['blocked'])

    async def test_citation_resolution_cannot_attribute_a_target_after_main_page_navigation(self):
        await self.page.locator('[data-testid="search-references-button"]').evaluate('e => e.remove()')
        await self.page.locator('#sources').evaluate('e => e.remove()')
        await self.page.locator('[data-testid="message_text_content"]').evaluate(
            'e => e.insertAdjacentHTML("beforeend", "<button data-citation>合成引用</button>")')
        async def moved(*_args, **_kwargs):
            await self.page.evaluate("history.pushState({}, '', '/chat/987654321')")
            return {'observed_url': 'https://foreign-source.example/not-target'}, None
        with patch.object(collector, '_resolve_citation_navigation', AsyncMock(side_effect=moved)):
            items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root,
                                                                        initial_evidence_file='answer.json')
        self.assertTrue(incomplete)
        self.assertEqual(shots, [])
        self.assertNotIn('foreign-source.example', json.dumps(items))
        self.assertTrue(items)
        self.assertTrue(all(item['evidence_file'] == 'answer.json' for item in items))

    async def test_real_citation_click_never_persists_dom_after_main_navigation(self):
        await self.page.locator('[data-testid="search-references-button"]').evaluate('e => e.remove()')
        await self.page.locator('#sources').evaluate('e => e.remove()')
        await self.page.locator('[data-testid="message_text_content"]').evaluate('''e => {
            const button = document.createElement('button');
            button.setAttribute('data-citation', ''); button.textContent = '合成引用';
            button.onclick = () => history.pushState({}, '', '/chat/987654321');
            e.append(button);
        }''')
        _items, shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertTrue(incomplete)
        self.assertEqual(shots, [])
        self.assertFalse(list(self.root.glob('citation-*.json')))
        self.assertFalse(list(self.root.glob('citation-*.png')))

    async def test_failure_checkpoint_cannot_save_after_target_navigation(self):
        expected = await collector.read_snapshot(self.page, self.config)
        guard = collector._SourceContextGuard(self.page, self.config, expected, self.root)
        guard.start()
        try:
            await self.page.evaluate("history.pushState({}, '', '/chat/987654321')")
            result = {'screenshots': []}
            with self.assertRaises(collector._SourceContextChanged):
                await collector._save_checkpoint(self.page, self.config, self.root, result, 'failure', source_guard=guard)
            self.assertEqual(result['screenshots'], [])
            self.assertFalse((self.root/'failure.json').exists())
            self.assertFalse((self.root/'failure.png').exists())
        finally:
            guard.finish()

    async def test_optional_answer_image_timeout_preserves_only_verified_target_dom(self):
        from playwright.async_api import TimeoutError
        expected = await collector.read_snapshot(self.page, self.config)
        guard = collector._SourceContextGuard(self.page, self.config, expected, self.root)
        guard.start()
        try:
            result = {'screenshots': [], 'answer_completed': True}
            with patch.object(self.page, 'screenshot', AsyncMock(side_effect=TimeoutError('synthetic timeout'))):
                await collector._save_checkpoint(self.page, self.config, self.root, result, 'answer', source_guard=guard)
            saved = json.loads((self.root/'answer.json').read_text(encoding='utf8'))
            self.assertEqual(saved['page_url'], URL)
            self.assertEqual(saved['answer'], ANSWER)
            self.assertTrue(result['answer_completed'])
            self.assertEqual(result['screenshots'], [])
            self.assertEqual(result['diagnostic_errors'][0]['error_type'], 'TimeoutError')
            self.assertFalse((self.root/'answer.png').exists())
        finally:
            guard.finish()

    async def test_reinspection_never_saves_off_target_source_or_after_checkpoint(self):
        original_path = self.root/'original/result.json'
        original_path.parent.mkdir()
        original = {'question': QUESTION, 'response': ANSWER, 'question_submitted': True,
                    'answer_completed': True, 'conversation_url': URL,
                    'environment': {'client_mode': 'consumer', 'model_label': '快速', 'session_slot': 'primary'}}
        original_path.write_text(json.dumps(original), encoding='utf8')
        original_bytes = original_path.read_bytes()
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True, output_root=self.root/'sidecars')
        await self.mutation("history.pushState({}, '', '/chat/987654321')")
        result, path = await collector.reinspect_existing_sources(config, original_path, page=self.page)
        self.assertEqual(result['failure_reason'], 'inspection_conversation_changed')
        self.assertEqual(result['source_context_binding']['status'], 'unverified')
        self.assertEqual(result['displayed_sources'], [])
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertTrue((path.parent/'inspection-before.json').exists())
        self.assertFalse(list(path.parent.glob('sources-*')))
        self.assertFalse(list(path.parent.glob('inspection-after*')))

    async def test_already_open_sources_reference_this_reinspection_manifest(self):
        await self.page.locator('[data-testid="search-references-button"]').evaluate('''e => {
            const panel=document.querySelector('#sources'); panel.hidden=false; panel.prepend(e);
        }''')
        original_path = self.root/'original/result.json'
        original_path.parent.mkdir()
        original = {'question': QUESTION, 'response': ANSWER, 'question_submitted': True,
                    'answer_completed': True, 'conversation_url': URL,
                    'environment': {'client_mode': 'consumer', 'model_label': '快速', 'session_slot': 'primary'}}
        original_path.write_text(json.dumps(original), encoding='utf8')
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True, output_root=self.root/'sidecars')
        result, path = await collector.reinspect_existing_sources(config, original_path, page=self.page)
        self.assertEqual(result['collection_status'], 'success')
        self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
        self.assertEqual(result['displayed_sources'][0]['evidence_file'], 'inspection-before.png')
        manifest = {entry['path']: entry for entry in result['evidence_manifest']}
        for item in result['displayed_sources']:
            for name in item['evidence_files']:
                self.assertIn(name, manifest)
                self.assertEqual(hashlib.sha256((path.parent/name).read_bytes()).hexdigest(), manifest[name]['sha256'])
        self.assertFalse((path.parent/'answer.png').exists())
        self.assertFalse(list(path.parent.glob('sources-*.png')))

    async def test_legacy_default_initial_evidence_name_remains_answer_png_and_names_are_closed(self):
        await self.page.locator('[data-testid="search-references-button"]').evaluate('''e => {
            const panel=document.querySelector('#sources'); panel.hidden=false; panel.prepend(e);
        }''')
        legacy = collector.CollectorConfig(question=QUESTION)
        items, shots, incomplete = await collector._expand_sources(self.page, legacy, self.root)
        self.assertFalse(incomplete)
        self.assertEqual(shots, [])
        self.assertEqual(items[0]['evidence_file'], 'answer.png')
        for name in ('../answer.png', 'foreign.png', '/tmp/answer.png', 'inspection-after.png', None):
            for expand in (collector._expand_sources, collector._expand_sources_bound):
                with self.subTest(name=name, helper=expand.__name__), self.assertRaisesRegex(ValueError, 'initial_source_evidence_file_invalid'):
                    await expand(self.page, legacy, self.root, initial_evidence_file=name)

    async def test_native_answer_png_timeout_uses_saved_json_for_initial_source_markers(self):
        from playwright.async_api import Page, TimeoutError
        html = '''<meta charset="utf-8"><button data-testid="workspace-switch-chip">豆包对话</button>
            <button data-testid="chat_input_action_model">快速</button><textarea></textarea>
            <div data-message-role="user"></div><div data-message-role="assistant" hidden>
            <div data-testid="message_text_content">合成目标回答。</div><button data-testid="message_action_copy">复制</button>
            <div data-testid="search-references"><button data-testid="search-references-button" onclick="window.sourceClicks++">参考资料</button>
            <a data-testid="reference-item" href="https://public-source.example/target">合成目标来源</a></div></div>
            <script>window.submissions=0;window.sourceClicks=0;
            document.querySelector('textarea').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();window.submissions++;
                document.querySelector('[data-message-role=user]').textContent=e.target.value;
                document.querySelector('[data-message-role=assistant]').hidden=false;e.target.value='';
                history.pushState({},'', '/chat/123456789');}};</script>'''
        async def create_context(*_args, **_kwargs):
            context = await self.browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html))
            return context
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=create_context))
        screenshot = Page.screenshot
        async def optional_timeout(page, **kwargs):
            if 'path' not in kwargs and kwargs.get('timeout') == 5000:
                raise TimeoutError('synthetic native answer image timeout')
            return await screenshot(page, **kwargs)
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True, output_root=self.root/'native',
            profile_dir=self.root/'synthetic-profile', stable_seconds=.1, timeout_seconds=5)
        with patch.object(Page, 'screenshot', autospec=True, side_effect=optional_timeout):
            result, path = await collector.collect_doubao_sources(config, playwright_factory=factory)
        self.assertEqual(result['collection_status'], 'success', result.get('failure_reason'))
        self.assertTrue(result['question_submitted'])
        self.assertTrue(result['answer_completed'])
        self.assertEqual(result['displayed_sources'][0]['evidence_file'], 'answer.json')
        self.assertFalse((path.parent/'answer.png').exists())
        self.assertEqual(json.loads((path.parent/'answer.json').read_text(encoding='utf8'))['answer'], ANSWER)
        manifest = {entry['path']: entry for entry in result['evidence_manifest']}
        self.assertIn('answer.json', manifest)
        self.assertNotIn('answer.png', manifest)
        self.assertEqual(hashlib.sha256((path.parent/'answer.json').read_bytes()).hexdigest(), manifest['answer.json']['sha256'])
        self.assertEqual(result['submission_receipt']['submission_attempts'], 1)

    async def test_global_history_source_controls_are_never_clicked_or_attributed(self):
        await self.page.evaluate('''() => {
            document.body.insertAdjacentHTML('afterbegin', '<aside data-plugin-identifier="search_query_result_block"><button data-copy-ignore onclick="window.historyClicked=true">参考资料</button><a data-testid="reference-item" href="https://unrelated-source.example/history">合成非目标来源</a></aside>');
        }''')
        items, _shots, incomplete = await collector._expand_sources(self.page, self.config, self.root)
        self.assertFalse(incomplete)
        self.assertEqual([item['href'] for item in items], ['https://public-source.example/target'])
        self.assertFalse(await self.page.evaluate('window.historyClicked === true'))

    async def test_verified_reinspection_binds_immutable_original_response_even_after_proven_progress_elision(self):
        await self.page.locator('[data-testid="message_text_content"]').evaluate(
            "e => e.setAttribute('data-streaming', 'false')")
        current = await collector.read_snapshot(self.page, self.config)
        progress = '继续核实候选官网。'
        original_answer = progress + '\n\n' + ANSWER
        original_snapshot = {**current, 'answer': original_answer,
            'answer_dom': '<div data-testid="message_text_content" data-streaming="false">'+progress+'</div>\n'+current['answer_dom']}
        original_path = self.root/'original/result.json'
        original_path.parent.mkdir()
        snapshot_bytes = json.dumps(original_snapshot, ensure_ascii=False).encode('utf8')
        (original_path.parent/'answer.json').write_bytes(snapshot_bytes)
        original = {'question': QUESTION, 'response': original_answer, 'question_submitted': True,
            'answer_completed': True, 'conversation_url': URL,
            'environment': {'client_mode': 'consumer', 'model_label': '快速', 'session_slot': 'primary'},
            'evidence_manifest': [{'path': 'answer.json', 'sha256': hashlib.sha256(snapshot_bytes).hexdigest()}]}
        original_path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf8')
        original_bytes = original_path.read_bytes()
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True, output_root=self.root/'sidecars')
        result, path = await collector.reinspect_existing_sources(config, original_path, page=self.page)
        self.assertEqual(result['collection_status'], 'success')
        binding = result['source_context_binding']
        self.assertEqual(binding['status'], 'verified')
        self.assertEqual(binding['target_answer_sha256'], hashlib.sha256(original_answer.encode()).hexdigest())
        self.assertEqual(binding['target_question_sha256'], hashlib.sha256(QUESTION.encode()).hexdigest())
        self.assertEqual(binding['canonical_url_sha256'], hashlib.sha256(URL.encode()).hexdigest())
        proof = json.loads((path.parent/binding['evidence_file']).read_text(encoding='utf8'))
        self.assertFalse(proof['blocked'])
        self.assertTrue(proof['source_phase_completed'])
        self.assertEqual(result['response'], original_answer)
        self.assertEqual(original_path.read_bytes(), original_bytes)
        entry = next(item for item in result['evidence_manifest'] if item['path'] == binding['evidence_file'])
        self.assertEqual(entry['sha256'], hashlib.sha256((path.parent/binding['evidence_file']).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
