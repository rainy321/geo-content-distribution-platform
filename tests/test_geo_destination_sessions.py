"""Customer onboarding checks using isolated HTML and temporary task stores."""
import asyncio
import copy
import hashlib
import html
import json
import threading
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import services
from services import geo_destination_browser as browser
from tests import test_geo_destination_browser_gates as gates
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_content_loop import ResearchFixture
from tests.test_geo_destinations import config as target_config
from services import geo_destination_service as destinations

EDITOR_URL, CONFIG, ARTICLE, BIND_WECHAT, iframe = (
    gates.EDITOR_URL, gates.CONFIG, gates.ARTICLE, gates.BIND_WECHAT, gates.iframe)


LOGIN_URL = 'https://editor.gates.example.org/login'
EMPTY = '<input id="title" type="text"><textarea id="body"></textarea>'


def csdn_document(body='', extra='', images=()):
    media = ''.join('<img src="' + html.escape(source, quote=True) + '">' for source in images)
    return '''<div class="article-bar__title-display" onclick="this.style.display='none';document.querySelector('input').style.display='block'">标题</div>
        <input type="text" class="article-bar__title article-bar__title--input text-input" style="display:none" value="【无标题】">
        <pre contenteditable="true" style="min-height:100px" class="editor__inner markdown-highlighting"><div class="cledit-section">''' + html.escape(body) + media + '''</div></pre>
        <button class="navigation_bar_more_actions_trigger" onclick="const popup=document.querySelector('.more-actions-popup');popup.hidden=!popup.hidden">更多操作</button>
        <div class="more-actions-popup" hidden><button onclick="this.parentElement.hidden=true;document.getElementById('import-markdown-file-input').click()">导入</button></div>
        <input id="import-markdown-file-input" type="file" accept=".md" hidden>
        <script>
        window.importCount=0;window.bodyInputEvents=0;
        window.originalDocument={title:document.querySelector('input').value,text:document.querySelector('pre').textContent};
        document.querySelector('pre').addEventListener('input',()=>window.bodyInputEvents++);
        document.getElementById('import-markdown-file-input').addEventListener('change',event=>{
            window.importCount++;const file=event.currentTarget.files[0];
            window.importedName=file.name;window.importedMime=file.type;
            const reader=new FileReader();
            reader.onload=loaded=>{
                const text=(loaded.target.result+'\\n').replace(/\\n\\n$/,'\\n');
                const section=document.createElement('div');section.className='cledit-section';section.textContent=text;
                document.querySelector('pre').replaceChildren(section);window.syntheticEditorModel=text;
                window.newDocument={articleid:null,text};
                const title=document.querySelector('input');title.value=file.name.replace(/\\.[^.]+$/,'');
                title.style.display='none';document.querySelector('.article-bar__title-display').style.display='block';
                event.target.value='';
            };
            reader.readAsText(file);
        });
        </script>''' + extra


class DestinationSessionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = gates.DestinationBrowserGateTests.asyncSetUp
    load = gates.DestinationBrowserGateTests.load

    async def start_session(self, *, login='', editor=EMPTY, article=None, progress=None, session_config=None,
                            initial_wait_seconds=10):
        configuration = {**CONFIG, 'login_url': LOGIN_URL, **(session_config or {})}
        self.documents[LOGIN_URL] = '<!doctype html><body>' + login + '</body>'
        self.documents[configuration['editor_url']] = '<!doctype html><body>' + editor + '</body>'
        self.events, self.check = [], asyncio.Event()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(TemporaryDirectory())
        self.stack.enter_context(patch('playwright.async_api.async_playwright', return_value=AsyncMock()))
        self.stack.enter_context(patch('services.browser_runtime.launch_context', AsyncMock(return_value=self.context)))

        def save(value):
            self.events.append(copy.deepcopy(value))
            if progress:
                progress(value)

        self.session_task = asyncio.create_task(browser.open_session(
            {'config': configuration}, Path(temporary) / 'profile', 'isolated-fixture',
            article=article, progress=save, check_requested=self.check, wait_seconds=20))
        self.addAsyncCleanup(self.cancel_session)
        await self.wait_until(lambda: self.events and self.events[-1]['destination_session']['can_check'],
                              timeout=initial_wait_seconds)
        return self.events[-1]['destination_session']

    async def cancel_session(self):
        if not self.session_task.done():
            self.session_task.cancel()
        await asyncio.gather(self.session_task, return_exceptions=True)

    async def wait_until(self, predicate, *, timeout=10):
        # The caller may bound an initial multi-stage import separately. Later
        # checks keep this budget; product waits and readiness stay unchanged.
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if self.session_task.done():
                await self.session_task
                self.fail('Session ended before the expected fixture observation')
            if asyncio.get_running_loop().time() >= deadline:
                self.fail('Timed out waiting for isolated fixture state')
            await asyncio.sleep(.01)

    async def request_check(self):
        count = len(self.events)
        self.check.set()
        await self.wait_until(lambda: len(self.events) > count and self.events[-1]['destination_session']['state'] != 'checking')
        return self.events[-1]['destination_session']

    async def test_login_prompt_is_explicit_and_manual_close_never_authenticates(self):
        observation = await self.start_session(login='<p>请先登录</p><input type="password">')
        self.assertEqual(observation['state'], 'login_required')
        self.assertTrue(observation['can_check'])
        self.assertEqual(len(self.context.pages), 1)
        await self.context.close()
        result = await self.session_task
        self.assertIsNone(result['session_saved'])
        self.assertEqual(result['destination_session']['state'], 'window_closed')
        self.assertEqual(result['destination_session']['last_state'], 'login_required')
        self.assertFalse(result['destination_session']['editor_ready'])

    async def test_explicit_check_uses_same_context_and_preserves_original_verification_page(self):
        await self.start_session(login='<h1>安全验证</h1><p>请完成安全验证</p>')
        original = self.page
        self.assertEqual(self.events[-1]['destination_session']['state'], 'challenge')
        result = await self.request_check()
        self.assertEqual(result['state'], 'editor_ready')
        finished = await self.session_task
        self.assertEqual(finished['close_reason'], 'editor_checked')
        self.assertIsNone(finished['session_saved'])
        # Page remains at its original entry until normal context shutdown.
        self.assertEqual(original.url, LOGIN_URL)

    async def test_cached_binding_prompt_is_rechecked_in_one_fresh_tab(self):
        await self.start_session(login=iframe(BIND_WECHAT), editor=iframe(BIND_WECHAT) + EMPTY)
        self.assertEqual(self.events[-1]['destination_session']['state'], 'binding_required')
        self.assertEqual(self.events[-1]['destination_session']['binding_kind'], 'wechat')
        first = await self.request_check()
        self.assertEqual(first['state'], 'binding_required')
        self.assertEqual(len(self.context.pages), 2)
        # Normal binding completed elsewhere. The old iframe is still cached.
        self.documents[EDITOR_URL] = '<body>' + EMPTY + '</body>'
        second = await self.request_check()
        self.assertEqual(second['state'], 'editor_ready')
        await self.session_task
        self.assertEqual(self.page.url, LOGIN_URL)

    async def test_generic_unconfigured_hidden_and_tutorial_fields_never_claim_ready(self):
        observation = await self.start_session(editor='<input id="title" type="text" hidden><pre contenteditable="true" id="body">平台首次教程</pre>')
        self.assertFalse(observation['editor_ready'])
        result = await self.request_check()
        self.assertEqual(result['state'], 'action_required')
        self.assertFalse(result['editor_ready'])
        self.assertEqual(await self.context.pages[1].locator('#body').inner_text(), '平台首次教程')
        config = {'editor_url': EDITOR_URL, 'title_selector': '', 'body_selector': ''}
        value = await browser._observe_session(self.context.pages[1], config, inspect_editor=True)
        self.assertEqual(value['state'], 'action_required')

    async def test_visible_frame_failure_is_unknown_and_never_leaks_private_exception(self):
        await self.load(iframe('合成加载页'))
        frame = self.page.frames[1]
        with patch.object(frame, 'evaluate', AsyncMock(side_effect=RuntimeError('PRIVATE_ACCOUNT_AND_URL'))):
            result = await browser._observe_session(self.page, CONFIG, inspect_editor=True)
        self.assertEqual(result['state'], 'action_required')
        self.assertNotIn('PRIVATE', str(result))

    async def test_zero_write_binding_can_continue_once_and_written_page_only_rechecks(self):
        await self.start_session(editor=iframe(BIND_WECHAT) + EMPTY, article=ARTICLE)
        self.assertFalse(self.events[-1]['destination_session']['content_write_attempted'])
        original = self.page
        self.documents[EDITOR_URL] = '<body>' + EMPTY + '</body>'
        with patch.object(browser, 'fill_editor', wraps=browser.fill_editor) as fill:
            result = await self.request_check()
            self.assertTrue(result['content_write_attempted'])
            self.assertEqual(fill.await_count, 1)
            editor = self.context.pages[1]
            self.assertEqual(await editor.locator('#title').input_value(), ARTICLE['title'])
            await editor.locator('#title').fill('客户手动修改的标题')
            with patch.object(editor, 'goto', AsyncMock(side_effect=AssertionError('No navigation after a write'))):
                await self.request_check()
                await self.request_check()
            self.assertEqual(fill.await_count, 1)
            self.assertEqual(await editor.locator('#title').input_value(), '客户手动修改的标题')
            self.assertEqual(len(self.context.pages), 2)
            self.assertEqual(original.url, EDITOR_URL)

    async def test_initial_fill_write_marker_precedes_first_write_and_never_replays(self):
        from playwright.async_api import Locator
        snapshots = []
        writes = []
        def before_progress(value):
            if value['destination_session']['content_write_attempted'] and not snapshots:
                snapshots.append('write_marker')
        original_fill = Locator.fill
        async def guarded_fill(field, value, **kwargs):
            self.assertEqual(snapshots, ['write_marker'])
            writes.append(value)
            return await original_fill(field, value, **kwargs)
        with patch.object(Locator, 'fill', guarded_fill):
            await self.start_session(article=ARTICLE, progress=before_progress)
        self.assertEqual(snapshots, ['write_marker'])
        self.assertEqual(writes, [ARTICLE['title'], ARTICLE['content']])
        self.assertTrue(self.events[-1]['destination_session']['content_write_attempted'])
        with patch.object(browser, 'fill_editor', AsyncMock(side_effect=AssertionError('must not replay'))):
            await self.request_check()
        self.assertEqual(len(self.context.pages), 1)
        self.assertEqual(await self.page.locator('#title').input_value(), ARTICLE['title'])

    async def test_failed_persistence_prevents_first_editor_write(self):
        await self.load()
        with self.assertRaisesRegex(RuntimeError, 'fixture persistence failed'):
            await browser.fill_editor(self.page, CONFIG, ARTICLE,
                before_write=lambda: (_ for _ in ()).throw(RuntimeError('fixture persistence failed')))
        self.assertEqual(await self.page.locator('#title').input_value(), '')
        self.assertEqual(await self.page.locator('#body').input_value(), '')

    async def test_partial_write_failure_stays_read_only_on_every_check(self):
        from playwright.async_api import Locator
        original_fill = Locator.fill
        async def fail_body(field, value, **kwargs):
            if await field.evaluate('el => el.id') == 'body':
                raise RuntimeError('PRIVATE_AUTOSAVE_RESPONSE')
            return await original_fill(field, value, **kwargs)
        with patch.object(Locator, 'fill', fail_body):
            await self.start_session(article=ARTICLE)
        self.assertTrue(self.events[-1]['destination_session']['content_write_attempted'])
        self.assertNotIn('PRIVATE', str(self.events))
        self.assertEqual(await self.page.locator('#title').input_value(), ARTICLE['title'])
        self.assertEqual(await self.page.locator('#body').input_value(), '')
        with patch.object(browser, 'fill_editor', AsyncMock(side_effect=AssertionError('No partial-fill replay'))):
            await self.request_check()
        self.assertEqual(len(self.context.pages), 1)
        self.assertEqual(await self.page.locator('#body').input_value(), '')

    async def test_challenge_and_account_restriction_are_separate_safe_phases(self):
        for text, state in (('请完成安全验证', 'challenge'), ('访问受限', 'action_required')):
            with self.subTest(text=text):
                await self.load(iframe(text + ' PRIVATE_ACCOUNT'))
                result = await browser._observe_session(self.page, CONFIG, inspect_editor=True)
                self.assertEqual(result['state'], state)
                self.assertNotIn('PRIVATE', str(result))

    async def test_manual_content_in_check_tab_prevents_refresh_or_second_tab(self):
        await self.start_session(editor=iframe(BIND_WECHAT) + EMPTY)
        await self.request_check()
        editor = self.context.pages[1]
        await editor.locator('#body').fill('客户在检查页手动写入的草稿')
        with patch.object(editor, 'goto', AsyncMock(side_effect=AssertionError('Do not destroy a manual draft'))):
            result = await self.request_check()
        self.assertEqual(result['state'], 'binding_required')
        self.assertEqual(await editor.locator('#body').input_value(), '客户在检查页手动写入的草稿')
        self.assertEqual(len(self.context.pages), 2)

    async def test_csdn_fixed_adapter_can_report_verified_welcome_without_generic_guessing(self):
        await self.start_session(editor='<p>未配置通用选择器</p>')
        adapter = SimpleNamespace(supports=lambda _: True,
            probe_editor=AsyncMock(return_value={'phase': 'editor_ready', 'editor_ready': True,
                'body_state': 'welcome_example', 'message': '编辑器可用；完整首次示例已识别，可将已审稿文章正常导入为新文档。'}))
        self.stack.enter_context(patch.object(services, 'csdn_editor', adapter))
        result = await self.request_check()
        self.assertEqual(result['state'], 'editor_ready')
        self.assertEqual(result['body_state'], 'welcome_example')
        self.assertIn('首次示例', result['message'])
        adapter.probe_editor.assert_awaited_once()
        await self.session_task

    async def test_real_fixed_csdn_adapter_is_dispatched_without_generic_selectors(self):
        from services import csdn_editor
        await self.start_session(editor=csdn_document(), article=ARTICLE, session_config={
            'editor_url': 'https://editor.csdn.net/md/', 'title_selector': '', 'body_selector': ''},
            # Isolated Windows profiling completed one import in 12.1s: eleven
            # exact field snapshots plus gate checks and full readback. Give
            # only this first import headroom; every later check remains 10s.
            initial_wait_seconds=30)
        self.assertTrue(self.events[-1]['destination_session']['content_write_attempted'])
        self.assertEqual(self.events[-1]['state'], 'filled')
        self.assertEqual(await self.page.locator(csdn_editor.TITLE).input_value(), ARTICLE['title'])
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(), ARTICLE['content'] + '\n')
        self.assertEqual(await self.page.evaluate('window.syntheticEditorModel'), ARTICLE['content'] + '\n')
        self.assertEqual(await self.page.evaluate('window.importCount'), 1)
        self.assertEqual(await self.page.evaluate('window.bodyInputEvents'), 0)
        self.assertEqual(await self.page.evaluate('window.importedMime'), 'text/markdown')
        self.assertRegex(await self.page.evaluate('window.importedName'), r'^geo-import-[a-f0-9]{32}\.md$')
        self.assertEqual(await self.page.evaluate('window.originalDocument.text'), '')
        self.assertIsNone(await self.page.evaluate('window.newDocument.articleid'))
        with patch.object(csdn_editor, 'fill_editor', AsyncMock(side_effect=AssertionError('No CSDN replay'))):
            await self.request_check()
        self.assertEqual(await self.page.evaluate('window.importCount'), 1)
        self.assertEqual(len(self.context.pages), 1)

    async def test_csdn_cached_binding_with_verified_welcome_can_refresh_same_check_tab(self):
        from services import csdn_editor
        welcome = '隔离测试的完整欢迎示例，要求全文匹配。'
        media = [['IMG', f'https://example.invalid/welcome-{index}.png', None, None] for index in range(4)]
        images = [entry[1] for entry in media]
        url = 'https://editor.csdn.net/md/'
        with patch.object(csdn_editor, 'WELCOME_SHA256', hashlib.sha256(welcome.encode()).hexdigest()), \
                patch.object(csdn_editor, 'WELCOME_MEDIA_SHA256', hashlib.sha256(json.dumps(media, separators=(',', ':')).encode()).hexdigest()):
            await self.start_session(login=iframe(BIND_WECHAT),
                editor=csdn_document(welcome, iframe(BIND_WECHAT), images),
                session_config={'editor_url': url, 'title_selector': '', 'body_selector': ''})
            self.assertEqual((await self.request_check())['state'], 'binding_required')
            self.assertEqual(len(self.context.pages), 2)
            self.documents[url] = csdn_document(welcome, images=images)
            result = await self.request_check()
            self.assertEqual(result['state'], 'editor_ready')
            self.assertEqual(result['body_state'], 'welcome_example')
            await self.session_task
            self.assertEqual(self.page.url, LOGIN_URL)


class NativeDestinationSignalTests(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.target = destinations.save_target(self.database, self.cycle['project_id'], target_config())
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def test_signal_is_same_task_coalesced_and_progress_is_durable_without_private_extras(self):
        entered, checked = threading.Event(), threading.Event()
        counts = []
        async def session(target, profile, executable, **kwargs):
            progress, signal = kwargs['progress'], kwargs['check_requested']
            observation = browser._session_state('binding_required', binding_kind='wechat')
            progress({'state': 'login_open', 'message': observation['message'],
                'destination_session': {**observation, 'private_html': 'PRIVATE_DO_NOT_PERSIST'}})
            entered.set()
            while True:
                await signal.wait()
                signal.clear()
                counts.append(1)
                await asyncio.sleep(.05)
                progress({'state': 'login_open', 'message': observation['message'], 'destination_session': observation})
                checked.set()
        with patch('services.geo_destination_browser.open_session', session):
            started = self.jobs.open_destination(self.cycle['id'], self.target['id'])
            self.assertTrue(entered.wait(3))
            identifier = started['id']
            first = self.jobs.check_destination_session(identifier)
            second = self.jobs.check_destination_session(identifier)
            self.assertEqual(first, second)
            self.assertTrue(first['check_requested'])
            record = self.jobs.get_task(identifier)
            self.assertEqual(record['cycle_id'], self.cycle['id'])
            self.assertEqual(record['target_id'], self.target['id'])
            self.assertEqual(record['progress']['destination_session']['state'], 'checking')
            self.assertFalse(record['progress']['destination_session']['can_check'])
            self.assertNotIn('PRIVATE', json.dumps(record))
            self.assertTrue(checked.wait(3))
            self.assertEqual(len(counts), 1)
            self.assertEqual(self.jobs.get_task(identifier)['status'], 'running')
            self.assertIn('error', self.jobs.check_destination_session('old-destination-task'))
            self.jobs.stop_task()
            self.jobs.worker.join(5)
        record = self.jobs.get_task(identifier)
        self.assertEqual(record['status'], 'stopped')
        self.assertFalse(record['progress']['destination_session']['can_check'])
        self.assertFalse(record['progress']['destination_session']['window_open'])
        self.assertIn('error', self.jobs.check_destination_session(identifier))

    def test_restored_pending_session_has_no_live_signal_and_cannot_continue(self):
        self.jobs.store.save({'id': 'old-open-session', 'kind': 'destination_login', 'status': 'running',
            'created_at': '2099-01-01T00:00:00+00:00', 'cycle_id': self.cycle['id'], 'target_id': self.target['id'],
            'progress': {'destination_session': browser._session_state('binding_required')}})
        restored = DesktopGeoJobs(self.root)
        record = restored.get_task('old-open-session')
        self.assertEqual(record['status'], 'needs_review')
        self.assertFalse(record['progress']['destination_session']['can_check'])
        self.assertFalse(record['progress']['destination_session']['window_open'])
        self.assertIn('error', restored.check_destination_session('old-open-session'))


