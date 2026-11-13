"""Strict local DOM regressions; no real account, challenge, or model request."""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import doubao_source_collector as collector


QUESTION = '合成本地来源绑定原题'
ANSWER = '合成本地完整回答'
CANONICAL = 'https://www.doubao.com/chat/987654321'


class ManualSourceReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def pipeline(self, variant='hydrate', *, changed_account=False, unsafe_after=False):
        answer = ANSWER + ('引用原题：' + QUESTION if variant == 'assistant_quote_only' else '')
        html = '''<!doctype html><meta charset="utf-8">
        <button data-testid="workspace-switch-chip">豆包对话</button>
        <button data-testid="chat_input_action_model">快速</button>
        <button aria-label="设置" onclick="openSettings()">设置</button>
        <div data-message-role="user" hidden></div>
        <div data-message-role="assistant" hidden>
          <div data-testid="message_text_content">ANSWER</div>
          <button data-testid="message_action_copy">复制</button>
          <button data-testid="search-references-button" onclick="sourceClicks++;document.querySelector('#sources').hidden=false">参考1篇资料</button>
          <div data-testid="search-references" id="sources" hidden>
            <a data-testid="reference-item" href="https://public-source.example/target">合成公开来源</a>
          </div>
        </div>
        <div class="tiptap" contenteditable="true"></div>
        <script>
        window.sends=0;window.sourceClicks=0;window.accountOpens=0;
        window.openSettings=()=>{
          accountOpens++;
          const dialog=document.createElement('div');dialog.setAttribute('role','dialog');dialog.tabIndex=-1;
          const handle=CHANGED && sends ? 'user_SyntheticSecond' : 'user_SyntheticFirst';
          dialog.innerHTML='<nav aria-label="设置分类"><button>合成昵称 豆包号：'+handle+'</button></nav>';
          dialog.addEventListener('keydown',event=>{if(event.key==='Escape' && !(UNSAFE && sends))dialog.remove()});
          document.body.append(dialog);
        };
        document.querySelector('[contenteditable]').addEventListener('keydown',event=>{
          if(event.key!=='Enter')return;
          event.preventDefault();sends++;
          history.replaceState({},'', '/chat/local_12345');
          const user=document.querySelector('[data-message-role=user]');user.textContent=event.target.innerText;user.hidden=false;
          const challenge=document.createElement('div');challenge.id='captcha-fixture';
          challenge.innerHTML='<button id="owner-completes-fixture">合成用户完成验证</button>';
          challenge.firstChild.onclick=()=>{
            challenge.remove();user.hidden=HIDE;history.pushState({},'', '/chat/987654321');
            document.querySelector('[data-message-role=assistant]').hidden=false;
          };
          document.body.append(challenge);event.target.innerText='';
        });
        </script>'''.replace('ANSWER', answer).replace('CHANGED', str(changed_account).lower()).replace('UNSAFE', str(unsafe_after).lower()).replace('HIDE', str(variant != 'literal_after_manual').lower())
        contexts, counts, phases = [], {}, []

        async def launch(*args, **kwargs):
            context = await self.browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
            close = context.close
            async def observed_close(*args, **kwargs):
                if context.pages:
                    counts.update(await context.pages[0].evaluate('({sends,sourceClicks,accountOpens})'))
                return await close(*args, **kwargs)
            context.close = observed_close
            contexts.append(context)
            return context

        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        inside, reads = False, 0
        original_read, original_readiness = collector.read_snapshot, collector._literal_source_readiness

        async def read(page, config, **kwargs):
            nonlocal reads
            if inside:
                reads += 1
                visible = variant not in ('missing', 'assistant_quote_only') and reads >= 2
                if variant == 'flicker':
                    visible = reads != 2
                await page.evaluate('(value)=>document.querySelector("[data-message-role=user]").hidden=!value', visible)
                if variant == 'wrong_question':
                    await page.evaluate('()=>{const e=document.querySelector("[data-message-role=user]");e.hidden=false;e.textContent="另一合成问题"}')
                elif variant == 'multiple_users':
                    await page.evaluate('()=>{const e=document.querySelector("[data-message-role=user]");e.hidden=false;document.body.append(e.cloneNode(true))}')
                elif variant == 'away_back':
                    await page.evaluate("()=>{history.pushState({},'', '/chat/555555');history.pushState({},'', '/chat/987654321')}")
            return await original_read(page, config, **kwargs)

        @asynccontextmanager
        async def readiness(*args, **kwargs):
            nonlocal inside
            inside = True
            try:
                budget = .8 if variant in ('missing', 'assistant_quote_only') else 3
                async with original_readiness(*args, **kwargs, timeout_seconds=budget) as snapshot:
                    yield snapshot
            finally:
                inside = False

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory)
            config = collector.CollectorConfig(question=QUESTION, output_root=root/'evidence', profile_dir=root/'profile',
                headed=True, manual_wait_seconds=5, strict_client_mode=True, account_probe_id='b'*32,
                session_slot='primary', stable_seconds=.01, timeout_seconds=5)
            with patch.object(collector, 'read_snapshot', read), patch.object(collector, '_literal_source_readiness', readiness):
                task = asyncio.create_task(collector.collect_doubao_sources(config, playwright_factory=factory, progress=phases.append))
                try:
                    for _ in range(160):
                        if any(value.get('event') == 'manual_action_required' for value in phases):
                            break
                        if task.done():
                            self.fail(str(await task))
                        await asyncio.sleep(.05)
                    else:
                        self.fail('Local manual challenge was not reached')
                    # Simulate the owner outside the collector; this is local HTML.
                    await contexts[0].pages[0].locator('#owner-completes-fixture').click()
                    result, path = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            files = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.suffix == '.json'}
            self.assertEqual(counts['sends'], 1)
            self.assertTrue(result['answer_completed'], result)
            self.assertEqual(result['question_verification_method'],
                'literal_user_dom' if variant == 'literal_after_manual' else 'bound_local_to_canonical_after_manual')
            delivery = json.loads(files['submission-observed.json'])
            self.assertTrue(delivery['question_verified'])
            for name, value in files.items():
                self.assertNotIn(b'user_SyntheticFirst', value, name)
                self.assertNotIn(b'user_SyntheticSecond', value, name)
                self.assertNotIn('合成昵称'.encode(), value, name)
            return result, files, counts, reads

    async def test_strict_manual_hydration_reaches_guard_sources_and_same_context_account_after(self):
        result, files, counts, reads = await self.pipeline()
        self.assertEqual(result['collection_status'], 'success', result)
        self.assertEqual(result['source_context_binding']['status'], 'verified')
        self.assertEqual(result['environment']['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual([item.stage for item in result.account_observations], ['before_submission', 'after_answer'])
        self.assertEqual(counts, {'sends': 1, 'sourceClicks': 1, 'accountOpens': 2})
        self.assertEqual(result['displayed_sources'][0]['url'], 'https://public-source.example/target')
        readiness = json.loads(files['source-readiness.json'])
        self.assertEqual([item['question_verified'] for item in readiness['observations']], [False, True, True])
        self.assertEqual(reads, 3)
        self.assertTrue(json.loads(files['answer.json'])['question_verified'])

    async def test_two_literal_reads_must_be_consecutive_after_hydration_flicker(self):
        result, files, _, reads = await self.pipeline('flicker')
        self.assertEqual(result['collection_status'], 'success', result)
        self.assertEqual(reads, 4)
        self.assertEqual([item['question_verified'] for item in json.loads(files['source-readiness.json'])['observations']], [True, False, True, True])

    async def test_already_literal_strict_answer_does_not_enter_manual_fallback_readiness(self):
        result, files, counts, reads = await self.pipeline('literal_after_manual')
        self.assertEqual(result['collection_status'], 'success', result)
        self.assertEqual(result['source_context_binding']['status'], 'verified')
        self.assertEqual(result['environment']['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual(counts, {'sends': 1, 'sourceClicks': 1, 'accountOpens': 2})
        self.assertEqual(reads, 0)
        self.assertNotIn('source-readiness.json', files)

    async def test_missing_user_or_answer_quote_cannot_promote_receipt_into_literal_dom(self):
        for variant in ('missing', 'assistant_quote_only'):
            with self.subTest(variant=variant):
                result, files, counts, _ = await self.pipeline(variant)
                self.assertEqual(result['collection_status'], 'need_action')
                self.assertEqual(result['failure_reason'], 'source_context_changed_not_captured')
                self.assertEqual(counts, {'sends': 1, 'sourceClicks': 0, 'accountOpens': 1})
                self.assertEqual(result['environment']['account_identity_status'], 'unverified')
                self.assertNotIn('account_identity_evidence', result)
                self.assertNotIn('answer.json', files)
                self.assertNotIn('failure.json', files)
                self.assertEqual(json.loads(files['source-readiness.json'])['stop_reason'], 'literal_question_not_ready')

    async def test_wrong_question_multiple_users_or_away_then_back_never_click_sources(self):
        for variant in ('wrong_question', 'multiple_users', 'away_back'):
            with self.subTest(variant=variant):
                result, files, counts, _ = await self.pipeline(variant)
                self.assertEqual(result['collection_status'], 'need_action')
                self.assertEqual(counts, {'sends': 1, 'sourceClicks': 0, 'accountOpens': 1})
                self.assertNotIn('failure.json', files)
                readiness = json.loads(files['source-readiness.json'])
                self.assertEqual(readiness['status'], 'unverified')
                if variant == 'away_back':
                    self.assertTrue(readiness['navigation_away_seen'])

    async def test_account_changed_or_settings_not_closed_still_cannot_bind_identity(self):
        for options in ({'changed_account': True}, {'unsafe_after': True}):
            with self.subTest(options=options):
                result, files, counts, _ = await self.pipeline(**options)
                self.assertEqual(result['source_context_binding']['status'], 'verified')
                self.assertEqual(counts, {'sends': 1, 'sourceClicks': 1, 'accountOpens': 2})
                self.assertEqual(result['environment']['account_identity_status'], 'unverified')
                self.assertNotIn('account_identity_evidence', result)
                if options.get('unsafe_after'):
                    self.assertEqual(result['failure_reason'], 'account_menu_not_safely_closed')
                    self.assertNotIn('failure.json', files)

    async def test_changed_complete_answer_mode_model_or_end_state_fails_without_yield(self):
        from tests.test_doubao_manual_wait import FakePage, snapshot
        class ObservablePage(FakePage):
            def remove_listener(self, event, callback):
                if self.handlers.get(event) == callback:
                    del self.handlers[event]
        for change in ({'answer': '另一合成回答'}, {'client_mode': 'work'}, {'model_label': '深度'},
                       {'assistant_count': 2}, {'completion_marker': False}, {'busy': True}, {'challenge': True}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                page = ObservablePage(); page.navigate('https://www.doubao.com/chat/local_12345')
                config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True)
                receipt = collector.SubmissionReceipt(page, QUESTION, 0, baseline_user_count=0,
                    submitted_text=QUESTION, editor_readback=QUESTION)
                receipt.attempted(); receipt.observe(page, QUESTION, snapshot(page_url=page.main_frame.url))
                receipt.manual_action_seen = True; page.navigate(CANONICAL)
                expected = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0,
                    answer=ANSWER, assistant_count=1, completion_marker=True, challenge=False, client_mode='consumer', model_label='快速')
                current = {**expected, 'question_verified': True, 'user_message_count': 1, **change}
                with patch.object(collector, 'read_snapshot', AsyncMock(return_value=current)):
                    with self.assertRaises(collector._SourceContextChanged):
                        async with collector._literal_source_readiness(page, config, expected, Path(directory), receipt):
                            self.fail('Changed context yielded a source binding')
                proof = json.loads((Path(directory)/'source-readiness.json').read_text())
                self.assertEqual(proof['stop_reason'], 'completed_answer_context_changed')


class ReadinessBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def prepare(self):
        class Page:
            def __init__(self):
                self.main_frame = SimpleNamespace(url='https://www.doubao.com/chat/local_12345')
                self.handlers, self.closed = {}, False
            def on(self, event, callback):
                self.handlers.setdefault(event, []).append(callback)
            def remove_listener(self, event, callback):
                self.handlers[event].remove(callback)
            def navigate(self, url):
                self.main_frame.url = url
                for callback in list(self.handlers.get('framenavigated', [])):
                    callback(self.main_frame)
            def is_closed(self):
                return self.closed
        page = Page()
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True)
        receipt = collector.SubmissionReceipt(page, QUESTION, 0, baseline_user_count=0,
            submitted_text=QUESTION, editor_readback=QUESTION)
        receipt.attempted()
        receipt.observe(page, QUESTION, {'page_url': page.main_frame.url, 'question_verified': True, 'user_message_count': 1})
        receipt.manual_action_seen = True
        page.navigate(CANONICAL)
        expected = {'page_url': CANONICAL, 'question_verified': False, 'user_message_count': 0,
            'assistant_count': 1, 'answer': ANSWER, 'completion_marker': True, 'busy': False,
            'client_mode': 'consumer', 'model_label': '快速'}
        return page, config, receipt, expected

    async def test_missing_manual_receipt_scope_cannot_start_any_dom_read(self):
        for attribute, value in (('manual_action_seen', False), ('editor_readback_verified', False),
                                 ('baseline_user_count', None), ('attempts', 2),
                                 ('invalid_reason', 'conversation_navigation_changed'), ('observed_url', None)):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as directory:
                page, config, receipt, expected = self.prepare()
                setattr(receipt, attribute, value)
                read = AsyncMock()
                with patch.object(collector, 'read_snapshot', read), self.assertRaises(collector._SourceContextChanged):
                    async with collector._literal_source_readiness(page, config, expected, Path(directory), receipt):
                        self.fail('An ineligible receipt yielded')
                read.assert_not_awaited()
                self.assertEqual(len(page.handlers['framenavigated']), 1)

    async def test_read_errors_timeouts_bad_payload_and_closed_page_fail_safely(self):
        for case in ('error', 'timeout', 'payload', 'closed'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                page, config, receipt, expected = self.prepare()
                read = AsyncMock(return_value={'answer': None})
                if case in ('error', 'timeout'):
                    read.side_effect = RuntimeError('synthetic page read failed') if case == 'error' else asyncio.TimeoutError()
                if case == 'closed':
                    page.closed = True
                with patch.object(collector, 'read_snapshot', read), self.assertRaises(collector._SourceContextChanged):
                    async with collector._literal_source_readiness(page, config, expected, Path(directory), receipt):
                        self.fail('An unreadable context yielded')
                if case == 'closed':
                    read.assert_not_awaited()
                proof = json.loads((Path(directory)/'source-readiness.json').read_text())
                self.assertEqual(proof['status'], 'unverified')
                self.assertEqual(len(page.handlers['framenavigated']), 1)

    async def test_navigation_observation_remains_active_until_original_guard_is_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            page, config, receipt, expected = self.prepare()
            literal = {**expected, 'question_verified': True, 'user_message_count': 1}
            guard = None
            with patch.object(collector, 'read_snapshot', AsyncMock(return_value=literal)), self.assertRaises(collector._SourceContextChanged):
                async with collector._literal_source_readiness(page, config, expected, Path(directory), receipt) as ready:
                    guard = collector._SourceContextGuard(page, config, ready, Path(directory))
                    guard.start()
                    self.assertEqual(len(page.handlers['framenavigated']), 3)
                    page.navigate('https://www.doubao.com/chat/555555')
                    page.navigate(CANONICAL)
            self.assertTrue(guard.proof['blocked'])
            self.assertTrue(guard.proof['navigation_away_seen'])
            self.assertEqual(guard.finish()['status'], 'unverified')
            proof = json.loads((Path(directory)/'source-readiness.json').read_text())
            self.assertEqual(proof['status'], 'unverified')
            self.assertTrue(proof['navigation_away_seen'])
            self.assertEqual(len(page.handlers['framenavigated']), 1)

    async def test_audit_write_failure_cannot_escape_as_unguarded_generic_diagnostic_error(self):
        with tempfile.TemporaryDirectory() as directory:
            page, config, receipt, expected = self.prepare()
            receipt.manual_action_seen = False
            with patch.object(Path, 'write_text', side_effect=OSError('synthetic audit write failure')):
                with self.assertRaises(collector._SourceContextChanged):
                    async with collector._literal_source_readiness(page, config, expected, Path(directory), receipt):
                        self.fail('An ineligible request yielded')

