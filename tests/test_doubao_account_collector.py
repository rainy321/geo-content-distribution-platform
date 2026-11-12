"""Local DOM pipeline tests; no real Doubao question or account is used."""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.doubao_source_collector import CollectorConfig, collect_doubao_sources
from services import doubao_source_collector as collector


class AccountCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def run_fixture(self, *, changed=False, unsafe_close=False):
        # The empty user bubble is hidden until the one ordinary send operation.
        html = '''<!doctype html><meta charset="utf-8">
        <button data-testid="workspace-switch-chip">豆包对话</button>
        <button aria-label="设置" onclick="openSettings()">设置</button>
        <div data-message-role="user" hidden></div>
        <div data-message-role="assistant" hidden>
          <div data-testid="message_text_content">本地合成回答</div>
          <button data-testid="message_action_copy">复制</button>
        </div>
        <div class="tiptap" contenteditable="true"></div>
        <script>
        window.sends=0; window.accountOpens=0;
        window.openSettings=()=>{
          window.accountOpens++;
          const dialog=document.createElement('div');
          dialog.setAttribute('role','dialog');dialog.tabIndex=-1;
          const account=CHANGED && window.sends ? 'user_SyntheticSecond' : 'user_SyntheticFirst';
          dialog.innerHTML='<nav aria-label="设置分类"><button>合成昵称 豆包号：'+account+'</button></nav>';
          dialog.addEventListener('keydown',event=>{if(event.key==='Escape' && !UNSAFE)dialog.remove()});
          document.body.append(dialog);
        };
        document.querySelector('[contenteditable]').addEventListener('keydown',event=>{
          if(event.key!=='Enter')return;
          event.preventDefault();window.sends++;
          const user=document.querySelector('[data-message-role=user]');
          user.textContent=event.target.innerText;user.hidden=false;
          document.querySelector('[data-message-role=assistant]').hidden=false;
          event.target.innerText='';
        });
        </script>'''.replace('CHANGED', 'true' if changed else 'false').replace('UNSAFE', 'true' if unsafe_close else 'false')
        contexts = []

        async def launch(*args, **kwargs):
            context = await self.browser.new_context()
            await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
            contexts.append(context)
            return context

        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = CollectorConfig(question='本地账号绑定测试题', output_root=root/'evidence',
                profile_dir=root/'profile', account_probe_id='a'*32,
                session_slot='secondary', stable_seconds=.1, timeout_seconds=5)
            checkpoint_names = []
            original_checkpoint = collector._save_checkpoint

            async def checked_checkpoint(page, config, run_dir, result, name, **kwargs):
                group = page.locator('nav[aria-label="设置分类"], [role="group"][aria-label="设置分类"]')
                self.assertFalse(any([await group.nth(i).is_visible() for i in range(await group.count())]), name)
                checkpoint_names.append(name)
                return await original_checkpoint(page, config, run_dir, result, name, **kwargs)

            with patch.object(collector, '_save_checkpoint', checked_checkpoint):
                result, path = await collect_doubao_sources(config, playwright_factory=factory)
            saved = json.loads(path.read_bytes())
            files = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.suffix == '.json'}
            self.assertNotIn('failure', checkpoint_names)
            return result, saved, files

    async def test_same_context_handle_bound_without_raw_identifier_in_evidence(self):
        result, saved, files = await self.run_fixture()
        self.assertTrue(result['question_submitted'], result)
        self.assertTrue(result['answer_completed'], result)
        self.assertEqual(result['account_identity_evidence']['probe_id'], 'a'*32)
        self.assertEqual(result['environment']['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual([o.stage for o in result.account_observations], ['before_submission', 'after_answer'])
        self.assertEqual(saved, result)
        self.assertNotIn('account_observations', saved)
        for name, value in files.items():
            self.assertNotIn(b'user_SyntheticFirst', value, name)
            self.assertNotIn('合成昵称'.encode(), value, name)
        receipt = json.loads(files['submission-observed.json'])
        self.assertTrue(receipt['question_verified'])

    async def test_mid_probe_account_change_keeps_answer_but_cannot_bind_identity(self):
        result, saved, _ = await self.run_fixture(changed=True)
        self.assertTrue(result['answer_completed'], result)
        self.assertEqual(result['environment']['account_identity_status'], 'unverified')
        self.assertNotIn('account_identity_evidence', saved)
        self.assertNotEqual(result.account_observations[0].handle_sha256,
                            result.account_observations[1].handle_sha256)

    async def test_unclosed_settings_stop_before_send_without_failure_screenshot(self):
        result, saved, files = await self.run_fixture(unsafe_close=True)
        self.assertFalse(result['question_submission_attempted'], result)
        self.assertEqual(saved['failure_reason'], 'account_menu_not_safely_closed')
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertNotIn('failure.png', result['screenshots'])
        self.assertNotIn('failure.json', files)
        for name, value in files.items():
            self.assertNotIn(b'user_SyntheticFirst', value, name)
