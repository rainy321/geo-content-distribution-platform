"""Routed synthetic settings only: no live login, network, or model questions."""
import asyncio
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from services import doubao_account_observer as observer
from services.doubao_account_identity import bind_probe_handle, compare_visible_handles


URL = 'https://www.doubao.com/chat/123456789'
HTML = '''<!doctype html><meta charset="utf-8">
<button id="settings" onclick="settingsClicks++; document.querySelector('#settings-dialog').hidden=false">设置</button>
<button id="send" onclick="sendClicks++">发送</button><textarea>保留的合成草稿</textarea>
<div role="dialog" id="settings-dialog" tabindex="-1" hidden>
 <h2>设置面板</h2>
 <aside class="setting-columns-navigation-synthetic"><div class="contents">
 <nav aria-label="设置分类" class="setting-columns-tabs-synthetic">
  <button id="account" aria-label="合成昵称 豆包号：user_SyntheticA1">合成昵称 豆包号：user_SyntheticA1</button>
  <button>通用</button><button role="switch" aria-checked="false"></button>
 </nav></div></aside>
</div>
<script>
window.settingsClicks=0; window.closeClicks=0; window.sendClicks=0;
window.enterPresses=0; window.escapeOnDialog=0; window.closeBehavior='normal';
const panel=document.querySelector('#settings-dialog');
window.closePanel=()=>{ closeClicks++; if(closeBehavior==='normal') panel.hidden=true;
 else if(closeBehavior==='group-only') panel.querySelector('[aria-label="设置分类"]').hidden=true;
 else if(closeBehavior==='group-remove') panel.querySelector('[aria-label="设置分类"]').remove(); };
panel.addEventListener('keydown', e=>{if(e.key==='Escape'){escapeOnDialog += e.target===panel ? 1 : 0; closePanel()}});
document.addEventListener('keydown',e=>{if(e.key==='Enter') enterPresses++});
</script>'''


class DoubaoAccountObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.runtime = await async_playwright().start()
        self.addAsyncCleanup(self.runtime.stop)
        self.browser = await self.runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.requests = []

        async def route(request_route):
            self.requests.append((request_route.request.method, request_route.request.url))
            await request_route.fulfill(status=200, content_type='text/html; charset=utf-8', body=HTML)

        await self.page.route('**/*', route)
        await self.page.goto(URL)

    async def observe(self, **changes):
        options = dict(client_mode='consumer', session_slot='secondary', context_id='a' * 32,
                       profile_scope_sha256='b' * 64, timeout_ms=180)
        options.update(changes)
        return await observer.observe_official_visible_handle(self.page, **options)

    async def counts(self):
        return await self.page.evaluate('({settings:settingsClicks, close:closeClicks, send:sendClicks, enter:enterPresses, scopedEscape:escapeOnDialog})')

    async def assert_closed(self):
        self.assertFalse(await self.page.locator('nav[aria-label="设置分类"], [role="group"][aria-label="设置分类"]').is_visible())
        self.assertFalse(await self.page.get_by_role('dialog').is_visible())

    async def test_observes_hash_only_then_closes_without_send_storage_api_or_screenshot(self):
        await self.page.evaluate('''()=>{
            window.storageReads=0; window.fetchCalls=0;
            for(const name of ['localStorage','sessionStorage','indexedDB']) {
                Object.defineProperty(window,name,{get(){storageReads++; throw Error('storage forbidden')}});
            }
            window.fetch=()=>{fetchCalls++; throw Error('API forbidden')};
        }''')
        with patch.object(self.page, 'screenshot', AsyncMock(side_effect=AssertionError('no screenshot'))), \
                patch.object(self.page.keyboard, 'press', AsyncMock(side_effect=AssertionError('no global keyboard'))):
            value = await self.observe()
        self.assertEqual(value.handle_sha256,
                         hashlib.sha256(b'doubao-visible-handle:v1:user_SyntheticA1').hexdigest())
        self.assertEqual(await self.counts(), {'settings': 1, 'close': 1, 'send': 0, 'enter': 0, 'scopedEscape': 1})
        await self.assert_closed()
        self.assertEqual(self.requests, [('GET', URL)])
        self.assertEqual(await self.page.evaluate('({storage:storageReads, fetch:fetchCalls})'),
                         {'storage': 0, 'fetch': 0})
        self.assertEqual(await self.page.locator('textarea').input_value(), '保留的合成草稿')
        self.assertNotIn('user_SyntheticA1', json.dumps(value.safe_record()) + repr(value))

    async def test_missing_or_ambiguous_settings_does_not_interact(self):
        for mutation in ('remove', 'duplicate', 'hidden', 'disabled'):
            with self.subTest(mutation=mutation):
                await self.page.goto(URL)
                await self.page.locator('#settings').evaluate('''(e, action)=>{
                    if(action==='remove') e.remove();
                    if(action==='duplicate') e.after(e.cloneNode(true));
                    if(action==='hidden') e.hidden=true;
                    if(action==='disabled') e.disabled=true;
                }''', mutation)
                self.assertIsNone(await self.observe())
                self.assertEqual((await self.counts())['settings'], 0)
                self.assertEqual((await self.counts())['send'], 0)

    async def test_only_official_https_chat_and_current_consumer_are_eligible(self):
        for url in ('http://www.doubao.com/chat/', 'https://www.doubao.com.evil.test/chat/',
                    'https://www.doubao.com:444/chat/', 'https://www.doubao.com/signin',
                    'https://www.doubao.com/chat-fake/'):
            with self.subTest(url=url):
                await self.page.goto(url)
                self.assertIsNone(await self.observe())
                self.assertEqual((await self.counts())['settings'], 0)
        await self.page.goto(URL)
        for mode in ('work', 'unknown', None):
            self.assertIsNone(await self.observe(client_mode=mode))
        self.assertEqual((await self.counts())['settings'], 0)

    async def test_missing_ambiguous_hidden_or_invalid_account_still_closes(self):
        for mutation in ('remove', 'duplicate', 'hidden', 'invalid', 'misleading'):
            with self.subTest(mutation=mutation):
                await self.page.goto(URL)
                await self.page.locator('#account').evaluate('''(e, action)=>{
                    if(action==='remove') e.remove();
                    if(action==='duplicate') e.after(e.cloneNode(true));
                    if(action==='hidden') e.hidden=true;
                    if(action==='invalid') e.setAttribute('aria-label','合成昵称 豆包号：138****1234');
                    if(action==='misleading') e.setAttribute('aria-label','合成昵称 豆包号：user_a 豆包号：user_b');
                }''', mutation)
                self.assertIsNone(await self.observe())
                await self.assert_closed()
                self.assertEqual((await self.counts())['close'], 1)
                self.assertEqual((await self.counts())['send'], 0)

    async def test_visible_inner_text_is_supported_without_aria_label(self):
        await self.page.locator('#account').evaluate("e=>e.removeAttribute('aria-label')")
        self.assertIsNotNone(await self.observe())
        await self.assert_closed()

    async def test_real_nav_container_and_explicit_group_are_both_supported(self):
        self.assertEqual(await self.page.locator('[aria-label="设置分类"]').evaluate('e=>e.tagName'), 'NAV')
        self.assertIsNone(await self.page.locator('[aria-label="设置分类"]').get_attribute('role'))
        self.assertIsNotNone(await self.observe())
        await self.assert_closed()
        await self.page.locator('[aria-label="设置分类"]').evaluate('''e=>{
            const group=document.createElement('div');
            group.setAttribute('role','group'); group.setAttribute('aria-label','设置分类');
            group.append(...e.childNodes); e.replaceWith(group);
        }''')
        self.assertIsNotNone(await self.observe())
        await self.assert_closed()

    async def test_mixed_nav_and_explicit_group_are_ambiguous_and_fail_closed(self):
        await self.page.locator('[aria-label="设置分类"]').evaluate('''e=>{
            const group=document.createElement('div');
            group.setAttribute('role','group'); group.setAttribute('aria-label','设置分类');
            group.textContent='合成重复分类'; e.after(group);
        }''')
        with self.assertRaises(observer.AccountObservationSafetyError):
            await self.observe()
        self.assertEqual((await self.counts())['send'], 0)
        self.assertEqual((await self.counts())['close'], 0)

    async def test_distinct_handles_and_same_context_probe_pair_are_not_conflated(self):
        before = await self.observe(stage='before_submission', probe_id='c' * 32)
        after = await self.observe(stage='after_answer', probe_id='c' * 32)
        proof = bind_probe_handle(before, after, probe_id='c' * 32, session_slot='secondary',
                                  context_id='a' * 32, profile_scope_sha256='b' * 64,
                                  source='native_collector')
        self.assertEqual(proof['account_identity_status'], 'official_visible_handle_observed')
        await self.page.locator('#account').evaluate("e=>e.setAttribute('aria-label','同一合成昵称 豆包号：user_SyntheticB2')")
        changed = await self.observe(stage='after_answer', probe_id='c' * 32)
        self.assertEqual(compare_visible_handles(before, changed)['relation'], 'different')
        with self.assertRaises(ValueError):
            bind_probe_handle(before, changed, probe_id='c' * 32, session_slot='secondary',
                              context_id='a' * 32, profile_scope_sha256='b' * 64,
                              source='native_collector')
        self.assertEqual((await self.counts())['send'], 0)

    async def test_unique_named_close_is_scoped_and_preferred_over_escape(self):
        await self.page.locator('#settings-dialog').evaluate('''e=>e.insertAdjacentHTML('beforeend',
            '<button aria-label="关闭设置" onclick="closePanel()"></button>')''')
        self.assertIsNotNone(await self.observe())
        self.assertEqual((await self.counts())['scopedEscape'], 0)
        await self.assert_closed()

    async def test_failed_or_partial_close_raises_safe_privacy_error_once(self):
        for behavior in ('no-effect', 'group-only', 'group-remove'):
            with self.subTest(behavior=behavior):
                await self.page.goto(URL)
                await self.page.evaluate('(behavior)=>window.closeBehavior=behavior', behavior)
                with self.assertRaises(observer.AccountObservationSafetyError) as caught:
                    await self.observe()
                self.assertEqual(str(caught.exception), 'account_observation_settings_close_unverified')
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual((await self.counts())['close'], 1)
                self.assertNotIn('user_', str(caught.exception))

    async def test_duplicate_close_buttons_are_never_clicked_or_bypassed(self):
        await self.page.locator('#settings-dialog').evaluate('''e=>e.insertAdjacentHTML('beforeend',
            '<button aria-label="关闭设置" onclick="closePanel()"></button><button aria-label="关闭设置" onclick="closePanel()"></button>')''')
        with self.assertRaises(observer.AccountObservationSafetyError):
            await self.observe()
        self.assertEqual((await self.counts())['close'], 0)
        self.assertEqual((await self.counts())['scopedEscape'], 0)

    async def test_missing_or_ambiguous_group_or_containing_dialog_is_unsafe_after_click(self):
        for mutation in ('group-missing', 'group-duplicate', 'dialog-role-missing', 'nested-dialog'):
            with self.subTest(mutation=mutation):
                await self.page.goto(URL)
                await self.page.locator('#settings-dialog').evaluate('''(e, action)=>{
                    const group=e.querySelector('[aria-label="设置分类"]');
                    if(action==='group-missing') group.remove();
                    if(action==='group-duplicate') group.after(group.cloneNode(true));
                    if(action==='dialog-role-missing') e.removeAttribute('role');
                    if(action==='nested-dialog'){const outer=document.createElement('div'); outer.setAttribute('role','dialog'); e.before(outer); outer.append(e)}
                }''', mutation)
                with self.assertRaises(observer.AccountObservationSafetyError):
                    await self.observe()
                self.assertEqual((await self.counts())['send'], 0)

    async def test_preexisting_settings_cannot_flow_into_an_answer_screenshot(self):
        await self.page.locator('#settings-dialog').evaluate('e=>e.hidden=false')
        with self.assertRaises(observer.AccountObservationSafetyError):
            await self.observe()
        self.assertEqual((await self.counts())['settings'], 0)

    async def test_navigation_during_read_discards_observation_after_normal_close(self):
        real = observer.identity.observe_visible_handle
        def moved(*args, **kwargs):
            value = real(*args, **kwargs)
            return value
        original_close = observer._close_settings
        async def closed_then_moved(*args, **kwargs):
            await original_close(*args, **kwargs)
            await self.page.evaluate("history.pushState({},'', '/chat/987654321')")
        with patch.object(observer.identity, 'observe_visible_handle', side_effect=moved), \
                patch.object(observer, '_close_settings', side_effect=closed_then_moved):
            self.assertIsNone(await self.observe())
        await self.assert_closed()

    async def test_cancellation_after_open_closes_then_propagates(self):
        with patch.object(observer.identity, 'observe_visible_handle', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.observe()
        await self.assert_closed()
        self.assertEqual((await self.counts())['close'], 1)

    async def test_cancellation_before_group_resolution_recovers_only_scoped_close(self):
        with patch.object(observer, '_settings_group', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.observe()
        await self.assert_closed()
        self.assertEqual((await self.counts())['close'], 1)

    async def test_failed_close_overrides_cancellation_with_safe_exception(self):
        await self.page.evaluate("window.closeBehavior='no-effect'")
        with patch.object(observer.identity, 'observe_visible_handle', side_effect=asyncio.CancelledError):
            with self.assertRaises(observer.AccountObservationSafetyError) as caught:
                await self.observe()
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual((await self.counts())['close'], 1)

    async def test_invalid_native_scope_never_opens_settings(self):
        with self.assertRaises(ValueError):
            await self.observe(context_id='invalid')
        self.assertEqual((await self.counts())['settings'], 0)


if __name__ == '__main__':
    unittest.main()
