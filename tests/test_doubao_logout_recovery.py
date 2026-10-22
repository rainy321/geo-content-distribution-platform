"""Real headless DOM fixtures, locally fulfilled; never a live Doubao session."""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from services.doubao_source_collector import CollectorConfig, _blocked_reason, collect_doubao_sources, read_snapshot


HTML = '''<!doctype html><html lang="zh"><title>合成豆包界面</title><style>#editor{min-height:30px;border:1px solid #ddd}</style><body>
<button data-testid="chat_input_action_model" id="model">synthetic-model</button>
<div data-testid="login_modal" id="login" hidden>正常登录</div>
<div contenteditable="true" class="tiptap" id="editor"></div>
<div data-message-role="user" id="user" hidden><div data-testid="message_text_content" id="question"></div></div>
<div data-message-role="assistant" id="answer" hidden><div data-testid="message_text_content">合成完整回答，仅用于本地验证。</div><button data-testid="message_action_copy">复制</button></div>
<script>
history.replaceState(null,'','/chat/?from_logout=1');window.submissions=0;
document.getElementById('editor').addEventListener('keydown',e=>{
 if(e.key==='Enter'){
  e.preventDefault();window.submissions++;document.getElementById('question').textContent=e.target.innerText;
  document.getElementById('user').hidden=false;
  if(window.logoutAfterSend){document.getElementById('login').hidden=false;document.getElementById('model').hidden=true;}
  else document.getElementById('answer').hidden=false;
 }
});
</script></body></html>'''


class LogoutDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.contexts = []
        self.events = []

    def factory(self, html):
        async def create_context(*args, **kwargs):
            context = await self.browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
            self.contexts.append(context)
            return context
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=create_context))
        return factory

    def config(self):
        return CollectorConfig(question='合成测试问题', headed=True, manual_wait_seconds=None,
            stable_seconds=.1, timeout_seconds=2, output_root=self.root/'evidence', profile_dir=self.root/'profile')

    async def test_stale_marker_requires_real_chat_controls_and_never_overrides_auth_or_logout_path(self):
        page = await self.browser.new_page()
        await page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=HTML))
        await page.goto('https://www.doubao.com/chat/')
        value = await read_snapshot(page, self.config())
        self.assertTrue(value['session_reset'])
        self.assertTrue(value['chat_interface_ready'])
        self.assertIsNone(_blocked_reason(value))
        for command, reason in [
            ("document.getElementById('login').hidden=false", 'login_required'),
            ("document.getElementById('login').hidden=true;document.getElementById('model').hidden=true", 'session_redirected_to_logout'),
            ("document.getElementById('model').hidden=false;document.getElementById('editor').hidden=true", 'session_redirected_to_logout'),
            ("document.getElementById('editor').hidden=false;history.replaceState(null,'','/logout')", 'session_redirected_to_logout')]:
            await page.evaluate(command)
            self.assertEqual(_blocked_reason(await read_snapshot(page, self.config())), reason)

    async def collect_with_login(self, *, after_send=False, change_identity=False):
        html = HTML.replace('</script>', ("window.logoutAfterSend=true;" if after_send else
            "document.getElementById('editor').hidden=true;document.getElementById('model').hidden=true;") + '</script>')
        counts = []
        controls = []
        async def normal_login():
            page = self.contexts[0].pages[-1]
            counts.append(await page.evaluate('window.submissions'))
            await page.evaluate("document.getElementById('login').hidden=false")
            await asyncio.sleep(.15)
            await page.evaluate("document.getElementById('login').hidden=true;document.getElementById('model').hidden=false;document.getElementById('editor').hidden=false;" +
                ("history.replaceState(null,'','/chat/888888');" if change_identity else '') +
                ("document.getElementById('answer').hidden=false;" if after_send else ''))
        def progress(event):
            self.events.append(event)
            if event['event'] == 'manual_action_required':
                controls.append(asyncio.create_task(normal_login()))
        result, path = await collect_doubao_sources(self.config(), playwright_factory=self.factory(html), progress=progress)
        await asyncio.gather(*controls)
        return result, path, counts

    async def test_before_send_logout_waits_for_normal_login_and_submits_only_once(self):
        result, path, counts = await self.collect_with_login()
        self.assertEqual(counts, [0])
        self.assertTrue(result['answer_completed'], result)
        self.assertTrue(result['question_submitted'])
        self.assertEqual(result['submission_receipt']['submission_attempts'], 1)
        event = next(item for item in self.events if item['event'] == 'manual_action_required')
        self.assertFalse(event['question_submission_attempted'])
        self.assertFalse(event['question_submitted'])
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['manual_verification']['events'][0]['cleared'])

    async def test_after_send_auth_returns_same_literal_answer_without_resending(self):
        result, _, counts = await self.collect_with_login(after_send=True)
        self.assertEqual(counts, [1])
        self.assertTrue(result['answer_completed'], result)
        self.assertTrue(result['question_submitted'])
        self.assertEqual(result['submission_receipt']['submission_attempts'], 1)

    async def test_authentication_navigation_cannot_rebind_an_old_answer_or_resend(self):
        result, _, counts = await self.collect_with_login(after_send=True, change_identity=True)
        self.assertEqual(counts, [1])
        self.assertFalse(result['answer_completed'])
        self.assertEqual(result['submission_receipt']['submission_attempts'], 1)
        self.assertEqual(result['submission_receipt']['invalid_reason'], 'conversation_navigation_changed')

    async def test_cancel_after_send_keeps_attempt_and_receipt_in_saved_evidence(self):
        reached = asyncio.Event()
        html = HTML.replace('</script>', 'window.logoutAfterSend=true;</script>')
        def progress(event):
            self.events.append(event)
            if event['event'] == 'manual_action_required':
                reached.set()
        task = asyncio.create_task(collect_doubao_sources(self.config(), playwright_factory=self.factory(html), progress=progress))
        await asyncio.wait_for(reached.wait(), 10)
        # Cancel during the manual wait, after the real checkpoint completes,
        # rather than abandoning an in-flight Playwright screenshot future.
        await self.wait_for_checkpoint()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        result = json.loads(next((self.root/'evidence').glob('*/result.json')).read_text(encoding='utf-8'))
        self.assertTrue(result['question_submission_attempted'])
        self.assertTrue(result['question_submitted'])
        self.assertFalse(result['answer_completed'])
        self.assertEqual(result['manual_verification']['events'][0]['exit_reason'], 'cancelled')

    async def wait_for_checkpoint(self):
        async def wait():
            while not list((self.root/'evidence').glob('*/manual-action-1.json')):
                await asyncio.sleep(.02)
        await asyncio.wait_for(wait(), 10)

    async def test_close_before_send_logout_keeps_unsent_state_and_evidence(self):
        reached = asyncio.Event()
        html = HTML.replace('</script>', "document.getElementById('editor').hidden=true;document.getElementById('model').hidden=true;</script>")
        def progress(event):
            if event['event'] == 'manual_action_required':
                reached.set()
        task = asyncio.create_task(collect_doubao_sources(self.config(), playwright_factory=self.factory(html), progress=progress))
        await asyncio.wait_for(reached.wait(), 10)
        await self.wait_for_checkpoint()
        await self.contexts[0].pages[-1].close()
        result, path = await task
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertFalse(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertFalse(result['answer_completed'])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['manual_verification']['events'][0]['exit_reason'], 'window_closed')


if __name__ == '__main__':
    unittest.main()
