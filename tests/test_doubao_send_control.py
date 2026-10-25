"""Observed send-control DOM and single-attempt fixtures; never a live session."""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services import doubao_source_collector as collector
from services.desktop_geo_jobs import DesktopGeoJobs
from services.doubao_submission_transport import submission_text
from tests.test_doubao_manual_wait import FakePage, snapshot


BUTTON = '<button data-testid="chat_input_send_button" id="flow-end-msg-send" data-disabled="false" aria-disabled="false" data-loading="false" onclick="window.clicks++">↑</button>'


class SendControlDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        self.browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = collector.CollectorConfig(submission_format='verbatim_fenced')

    async def prepare(self, html):
        await self.page.set_content('<meta charset="utf-8"><script>window.clicks=0;</script>'+html)
        return await collector._fenced_send_control(self.page, self.config, self.root)

    async def test_exact_observed_button_is_bound_but_not_clicked_during_inspection(self):
        handle = await self.prepare(BUTTON)
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)
        proof = json.loads((self.root/'submission-control.json').read_text(encoding='utf-8'))
        self.assertEqual(proof['visible_count'], 1)
        self.assertEqual(proof['controls'][0]['test_id'], 'chat_input_send_button')
        self.assertTrue(proof['controls'][0]['enabled'])
        await handle.click()
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_missing_hidden_or_multiple_visible_buttons_block_before_any_click(self):
        for html in ('<button>发送</button>', BUTTON.replace('<button ', '<button hidden '), BUTTON+BUTTON):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, 'submission_send_button_not_unique_not_submitted'):
                await self.prepare(html)
            self.assertEqual(await self.page.evaluate('window.clicks'), 0)

    async def test_native_aria_data_disabled_or_loading_state_blocks_before_any_click(self):
        for html in (BUTTON.replace('<button ', '<button disabled '),
                     BUTTON.replace('aria-disabled="false"', 'aria-disabled="true"'),
                     BUTTON.replace('data-disabled="false"', 'data-disabled="true"'),
                     BUTTON.replace('data-loading="false"', 'data-loading="true"')):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, 'submission_send_button_not_ready_not_submitted'):
                await self.prepare(html)
            self.assertEqual(await self.page.evaluate('window.clicks'), 0)

    async def test_hidden_matching_copy_does_not_make_one_visible_control_ambiguous(self):
        handle = await self.prepare(BUTTON+BUTTON.replace('<button ', '<button hidden '))
        proof = json.loads((self.root/'submission-control.json').read_text(encoding='utf-8'))
        self.assertEqual(proof['matched_count'], 2)
        self.assertEqual(proof['visible_count'], 1)
        await handle.click()
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_replaced_button_cannot_retarget_the_already_bound_node(self):
        handle = await self.prepare(BUTTON)
        await self.page.evaluate('''() => {
            const original=document.querySelector('[data-testid="chat_input_send_button"]');
            original.replaceWith(original.cloneNode(true));
        }''')
        with self.assertRaises(Exception):
            await handle.click(timeout=100)
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)

    async def test_delayed_input_handler_settles_before_one_send_click(self):
        await self.page.set_content('''<textarea></textarea>
            <button data-testid="chat_input_send_button">↑</button>
            <script>
            window.ready=false; window.clicks=0; window.accepted=0;
            document.querySelector('textarea').addEventListener('input',()=>{
                setTimeout(()=>window.ready=true, 500);
            });
            document.querySelector('button').addEventListener('click',()=>{
                window.clicks++; if(window.ready) window.accepted++;
            });
            </script>''')
        editor = self.page.locator('textarea')
        text = submission_text('合成任务\n{"literal":true}', 'verbatim_fenced')
        await editor.fill(text)
        proof = await collector._fenced_editor_settle(editor, text, self.root)
        control = await collector._fenced_send_control(self.page, self.config, self.root)
        await control.click()
        self.assertEqual(await self.page.evaluate('({clicks, accepted})'), {'clicks': 1, 'accepted': 1})
        self.assertGreaterEqual(proof['waited_seconds'], 1.0)
        self.assertTrue(proof['unchanged_between_observations'])
        self.assertEqual(await editor.input_value(), text)

    async def test_changed_editor_during_settle_stops_without_edit_or_click(self):
        await self.page.set_content('''<textarea></textarea>
            <button onclick="window.clicks++">↑</button><script>window.clicks=0;
            document.querySelector('textarea').addEventListener('input',e=>{
                setTimeout(()=>e.target.value='外部改变的内容', 300);
            });</script>''')
        editor = self.page.locator('textarea')
        await editor.fill('original exact input')
        observed = await collector._fenced_editor_settle(editor, 'original exact input', self.root)
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)
        self.assertEqual(await editor.input_value(), '外部改变的内容')
        proof = json.loads((self.root/'editor-settle.json').read_text(encoding='utf-8'))
        self.assertTrue(proof['matched_before'])
        self.assertFalse(proof['matched_after'])
        self.assertEqual(observed, proof)


class FencedAttemptCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.question = '合成多行任务\n{"quoted":"literal"}'
        self.sent = submission_text(self.question, 'verbatim_fenced')
        self.page = FakePage()
        self.page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
        self.page.get_by_role = Mock()
        self.page.evaluate = AsyncMock(return_value='synthetic browser')
        self.editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(), evaluate=AsyncMock(return_value={
            'readback': self.sent, 'method': 'contenteditable_inner_text_exact', 'editor_dom': '<div></div>',
            'inner_text': self.sent, 'text_content': self.sent}))
        self.control = SimpleNamespace(click=AsyncMock(side_effect=RuntimeError('synthetic stop after click attempt')))

    async def collect(self, *, progress=None, writer=None, control_error=None):
        context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        config = collector.CollectorConfig(question=self.question, submission_format='verbatim_fenced',
            output_root=self.root/'geo-content/evidence', profile_dir=self.root/'profile', stable_seconds=.1, timeout_seconds=1)
        async def select(*_args):
            self.assertEqual(self.editor.evaluate.await_count, 3)
            if control_error:
                raise ValueError(control_error)
            return self.control
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', AsyncMock()), \
             patch.object(collector, '_first_visible', AsyncMock(side_effect=[None, self.editor])), \
             patch.object(collector, 'read_snapshot', AsyncMock(return_value=snapshot(challenge=False,
                 question_verified=False, user_message_count=0))), \
             patch.object(collector, '_save_checkpoint', AsyncMock()), \
             patch.object(collector, '_fenced_send_control', side_effect=select):
            if writer:
                with patch.object(collector, 'write_evidence_result', side_effect=writer):
                    return await collector.collect_doubao_sources(config, playwright_factory=factory, progress=progress)
            return await collector.collect_doubao_sources(config, playwright_factory=factory, progress=progress)

    async def test_editor_and_collector_native_checkpoints_precede_exactly_one_click(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        jobs.job = {'id': 'synthetic-fenced-checkpoint', 'kind': 'draft', 'status': 'running'}
        persisted = []
        def progress(event):
            if event['event'] == 'question_submission_started':
                self.control.click.assert_not_awaited()
                result = json.loads(Path(event['evidence_path']).read_text(encoding='utf-8'))
                proof = json.loads((Path(event['evidence_path']).parent/'editor-readback.json').read_text(encoding='utf-8'))
                self.assertTrue(proof['verified'])
                self.assertTrue(result['question_submission_attempted'])
                self.assertEqual(result['submission_transport']['method'], 'visible_send_button')
                jobs._collector_progress(event)
                self.assertTrue(jobs.store.read(jobs.job['id'])['submission_state']['question_submission_attempted'])
                persisted.append(True)
        async def click(*_args, **_kwargs):
            self.assertEqual(persisted, [True])
            raise RuntimeError('synthetic click result unknown')
        self.control.click.side_effect = click
        result, _ = await self.collect(progress=progress)
        self.control.click.assert_awaited_once_with(timeout=5000)
        self.editor.press.assert_not_awaited()
        self.assertTrue(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertEqual(result['failure_stage'], 'submit_question')

    async def test_unavailable_control_never_records_an_attempt_or_uses_enter(self):
        result, path = await self.collect(control_error='submission_send_button_not_unique_not_submitted')
        self.control.click.assert_not_awaited()
        self.editor.press.assert_not_awaited()
        self.assertFalse(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertEqual(result['failure_stage'], 'submission_control')
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertTrue(path.is_file())

    async def test_collector_storage_failure_prevents_click_and_enter(self):
        original = collector.write_evidence_result
        calls = []
        def writer(path, result):
            calls.append(True)
            if len(calls) == 1:
                raise OSError('synthetic checkpoint disk failure')
            return original(path, result)
        result, _ = await self.collect(writer=writer)
        self.control.click.assert_not_awaited()
        self.editor.press.assert_not_awaited()
        self.assertEqual(result['failure_stage'], 'submission_checkpoint')

    async def test_native_checkpoint_failure_prevents_click_and_enter(self):
        def progress(event):
            if event['event'] == 'question_submission_started':
                raise OSError('synthetic native checkpoint disk failure')
        result, _ = await self.collect(progress=progress)
        self.control.click.assert_not_awaited()
        self.editor.press.assert_not_awaited()
        self.assertEqual(result['failure_stage'], 'submission_checkpoint')

    async def test_send_operation_event_is_not_treated_as_delivery_receipt(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        jobs.job = {'id': 'synthetic-click-not-receipt', 'kind': 'draft', 'status': 'running'}
        jobs._collector_progress({'event': 'question_sent', 'question_submission_attempted': True,
                                  'question_submitted': False})
        value = jobs.get_task()
        self.assertIn('正在核对', value['message'])
        self.assertNotIn('问题已发送', value['message'])
        self.assertFalse(value['submission_state']['question_submitted'])


if __name__ == '__main__':
    unittest.main()
