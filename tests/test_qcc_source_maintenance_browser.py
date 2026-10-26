"""Synthetic page-only checks: no browser, network, real company or personal data."""
import asyncio
from contextlib import nullcontext
import json
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.qcc_source_maintenance_browser import _unique_visible, observe, prepare_form, open_session


class Element:
    def __init__(self, page, name, *, text='', value='', visible=True, enabled=True, action=None):
        self.page, self.name, self.text, self.value = page, name, text, value
        self.visible, self.enabled, self.action = visible, enabled, action

    async def is_visible(self): return self.visible
    async def is_enabled(self): return self.enabled
    async def input_value(self): return self.value
    async def inner_text(self, **_): return self.page.body
    async def wait_for(self, **_):
        if not self.visible: raise ValueError('synthetic hidden control')
    async def fill(self, value):
        self.page.fills.append((self.name, value)); self.value = value
    async def click(self):
        self.page.clicks.append(self.name)
        if self.action: self.action()


class Locator:
    def __init__(self, elements): self.elements = elements
    async def count(self): return len(self.elements)
    def nth(self, index): return self.elements[index]
    @property
    def first(self): return self.elements[0]
    def filter(self, *, has_text):
        return Locator([element for element in self.elements if matches(has_text, element.text)])
    async def inner_text(self, **kwargs): return await self.first.inner_text(**kwargs)
    async def wait_for(self, **kwargs): return await self.first.wait_for(**kwargs)


def matches(pattern, value):
    return bool(pattern.search(value)) if hasattr(pattern, 'search') else pattern == value


class Page:
    def __init__(self, *, url='https://help.qcc.com/spt/support/handle-dissent', body=None,
                 login=False, challenge=None, candidate_count=1, existing_company='',
                 existing_proposal='', toast=None, fee=False):
        self.url, self.explicit_body, self.phase = url, body, 'entry'
        self.login, self.toast, self.fee = login, toast, fee
        self.clicks, self.fills = [], []
        self.company = Element(self, 'company_input', value=existing_company)
        self.proposal = Element(self, 'public_proposal', value=existing_proposal)
        self.candidates = [Element(self, 'company_candidate', text='公司甲', action=lambda: setattr(self, 'phase', 'modules')) for _ in range(candidate_count)]
        self.challenges = [] if challenge is None else [Element(self, 'challenge', visible=challenge)]

    @property
    def body(self):
        if self.explicit_body is not None: return self.explicit_body
        return '公司甲 企业官网 ' + ('公开示例资料' if self.phase == 'verify' else '')

    def locator(self, selector):
        if selector == 'body': return Locator([Element(self, 'body')])
        if selector == 'em': return Locator(self.candidates)
        if selector == 'textarea:visible': return Locator([self.proposal])
        if 'captcha' in selector: return Locator(self.challenges)
        raise AssertionError('Unexpected selector: ' + selector)

    def get_by_placeholder(self, text, exact=False):
        if text != '请输入企业名称': raise AssertionError('Private input must never be selected')
        return Locator([self.company])

    def _data(self):
        self.phase = 'data'; self.url = 'https://help.qcc.com/spt/support/handle-dissent/data'

    def _verify(self):
        self.phase = 'verify'; self.url = 'https://help.qcc.com/spt/support/handle-dissent/verify'

    def get_by_text(self, text, exact=False):
        controls = []
        if self.login: controls.append(Element(self, 'login', text='扫码登录'))
        if self.toast: controls.append(Element(self, 'toast', text=self.toast))
        if self.phase in ('modules', 'selected'):
            controls.extend([Element(self, 'module_list', text='隐藏无数据模块'),
                             Element(self, 'module', text='企业官网', action=lambda: setattr(self, 'phase', 'selected'))])
        if self.phase == 'selected': controls.append(Element(self, 'module_next', text='已选企业官网，下一步', action=self._data))
        if self.phase == 'data': controls.append(Element(self, 'preview_next', text='下一步', action=self._verify))
        return Locator([control for control in controls if matches(text, control.text)])

    def get_by_role(self, role, name):
        controls = [Element(self, 'final_submit', text='确认提交')]
        if self.fee: controls.append(Element(self, 'payment', text='立即支付'))
        return Locator([control for control in controls if role == 'button' and matches(name, control.text)])

    async def wait_for_timeout(self, _): pass
    async def wait_for_url(self, expression, **_): assert expression.search(self.url)


def record(**changes):
    return dict(company_name='公司甲', module_label='企业官网', proposal='公开示例资料',
                stage='draft', read_only=False, **changes)


class QccBrowserSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_observe_outside_product_entry_requires_review(self):
        page = Page(url='https://www.qcc.com/firm_example.html')
        self.assertEqual((await observe(page, record()))['stage'], 'needs_manual_review')
        self.assertEqual(page.clicks + page.fills, [])

    async def test_prepare_does_not_operate_outside_allowed_correction_page(self):
        page = Page(url='https://example.invalid/product')
        result = await prepare_form(page, record(), before_write=lambda: self.fail('No write fence allowed'))
        self.assertEqual(result['stage'], 'needs_manual_review')
        self.assertEqual(page.clicks + page.fills, [])

    async def test_login_slider_and_visible_captcha_are_distinct_waits(self):
        cases = [(Page(login=True), 'awaiting_login'),
                 (Page(body='请拖动滑块完成拼图'), 'waiting_verification'),
                 (Page(challenge=True), 'waiting_verification')]
        for page, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual((await observe(page, record()))['stage'], expected)
                self.assertEqual(page.clicks + page.fills, [])

    async def test_hidden_captcha_does_not_block_a_normal_page(self):
        self.assertEqual((await observe(Page(challenge=False), record()))['stage'], 'needs_manual_input')

    async def test_provider_rejection_does_not_trigger_automated_workaround(self):
        page = Page(body='This browser or app may not be secure')
        self.assertEqual((await prepare_form(page, record(), before_write=lambda: self.fail()))['stage'], 'browser_rejected')
        self.assertEqual(page.clicks + page.fills, [])

    async def test_unbound_success_toast_is_not_credited_to_this_task(self):
        page = Page(toast='提交成功')
        self.assertEqual((await observe(page, record()))['stage'], 'needs_manual_review')
        self.assertEqual((await observe(page, record(), bound=True))['stage'], 'submitted_pending_review')

    async def test_readonly_record_never_prepares_a_form(self):
        page = Page()
        with self.assertRaises(ValueError):
            await prepare_form(page, {**record(), 'read_only': True}, before_write=lambda: self.fail())
        self.assertEqual(page.clicks + page.fills, [])

    async def test_different_existing_company_is_not_overwritten(self):
        page = Page(existing_company='公司乙')
        self.assertEqual((await prepare_form(page, record(), before_write=lambda: self.fail()))['stage'], 'needs_manual_review')
        self.assertEqual(page.company.value, '公司乙')
        self.assertEqual(page.clicks + page.fills, [])

    async def test_ambiguous_company_candidates_do_not_click_first(self):
        page = Page(candidate_count=2)
        with self.assertRaises(ValueError): await prepare_form(page, record(), before_write=lambda: self.fail())
        self.assertNotIn('company_candidate', page.clicks)

    async def test_unique_visible_ignores_hidden_first_candidate(self):
        page = Page(); hidden = Element(page, 'hidden', visible=False); visible = Element(page, 'visible')
        self.assertIs(await _unique_visible(Locator([hidden, visible])), visible)
        with self.assertRaises(ValueError): await _unique_visible(Locator([visible, Element(page, 'also_visible')]))

    async def test_different_existing_public_proposal_is_preserved(self):
        page = Page(existing_proposal='此前公开内容')
        result = await prepare_form(page, record(), before_write=lambda: self.fail('Must not write over existing proposal'))
        self.assertEqual(result['stage'], 'needs_manual_review')
        self.assertEqual(page.proposal.value, '此前公开内容')
        self.assertNotIn('public_proposal', [name for name, _ in page.fills])
        self.assertNotIn('preview_next', page.clicks)

    async def test_normal_preparation_stops_before_human_final_submission(self):
        page = Page(); fences = []
        result = await prepare_form(page, record(), before_write=lambda: fences.append(True))
        self.assertEqual(result['stage'], 'confirmation_ready')
        self.assertEqual(fences, [True])
        self.assertEqual(page.clicks, ['company_input', 'company_candidate', 'module', 'module_next', 'preview_next'])
        self.assertNotIn('final_submit', page.clicks)
        self.assertEqual(page.fills, [('company_input', '公司甲'), ('public_proposal', '公开示例资料')])

    async def test_observation_does_not_return_body_private_input_or_artifacts(self):
        page = Page(url='https://help.qcc.com/spt/support/handle-dissent/verify',
                    body='公司甲 企业官网 公开示例资料 PRIVATE_PERSON_VALUE PRIVATE_CONTACT_VALUE')
        result = await observe(page, record())
        self.assertEqual(result['stage'], 'confirmation_ready')
        self.assertLessEqual(set(result), {'stage', 'message', 'confirmation_seen'})
        rendered = json.dumps(result)
        for private in ['PRIVATE_PERSON_VALUE', 'PRIVATE_CONTACT_VALUE', 'body', 'screenshot']:
            self.assertNotIn(private, rendered)

    async def test_fee_page_is_a_stop_without_payment_click(self):
        page = Page(fee=True)
        self.assertEqual((await prepare_form(page, record(), before_write=lambda: self.fail()))['stage'], 'fee_required_stopped')
        self.assertEqual(page.clicks + page.fills, [])


class OpenSessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def assert_failed_preparation_keeps_window(self, *, initial_wait_fails):
        class SessionPage(Page):
            def __init__(self):
                super().__init__(); self.handlers = {}; self.closed = False
            def on(self, name, handler): self.handlers[name] = handler
            def is_closed(self): return self.closed
            def user_closes_window(self):
                self.closed = True
                self.handlers['close']()

        class Context:
            def __init__(self, page): self.pages = [page]; self.handlers = {}; self.close_calls = 0
            def on(self, name, handler): self.handlers[name] = handler
            async def close(self):
                self.close_calls += 1
                self.handlers['close']()

        class PlaywrightScope:
            async def __aenter__(self): return object()
            async def __aexit__(self, *_): pass

        page = SessionPage(); context = Context(page)
        check = asyncio.Event(); failure_seen = asyncio.Event(); inspected = asyncio.Event(); events = []

        def progress(value):
            events.append(value)
            if value['stage'] == 'needs_manual_review': failure_seen.set()

        async def observe_current(*_, **__):
            inspected.set()
            return {'stage': 'needs_manual_input', 'message': 'synthetic normal form'}

        initial = AsyncMock(side_effect=TimeoutError('synthetic SPA readiness timeout') if initial_wait_fails else None)
        preparation = AsyncMock(side_effect=TimeoutError('synthetic preparation timeout'))
        observation = AsyncMock(side_effect=observe_current)
        with patch('services.browser_runtime.launch_context', new=AsyncMock(return_value=context)), \
             patch('services.geo_test_runner.exclusive_lock', return_value=nullcontext()), \
             patch('services.qcc_source_maintenance_browser.wait_initial_ready', new=initial), \
             patch('services.qcc_source_maintenance_browser.observe', new=observation), \
             patch('services.qcc_source_maintenance_browser.prepare_form', new=preparation):
            task = asyncio.create_task(open_session(record(), Path('synthetic-profile'), None,
                progress=progress, check_requested=check, playwright_factory=PlaywrightScope))
            try:
                await asyncio.wait_for(failure_seen.wait(), timeout=1)
                self.assertFalse(task.done())
                self.assertFalse(page.closed)
                self.assertEqual(context.close_calls, 0)
                self.assertEqual(events[-1]['stage'], 'needs_manual_review')
                self.assertTrue(events[-1]['window_open'])
                self.assertTrue(events[-1]['inspection_only'])
                expected_preparations = 0 if initial_wait_fails else 1
                self.assertEqual(preparation.await_count, expected_preparations)
                before = observation.await_count
                inspected.clear(); check.set()
                await asyncio.wait_for(inspected.wait(), timeout=1)
                self.assertEqual(observation.await_count, before + 1)
                self.assertEqual(preparation.await_count, expected_preparations)
                self.assertFalse(task.done())
                self.assertEqual(context.close_calls, 0)
                self.assertTrue(events[-1]['window_open'])
            finally:
                page.user_closes_window()
                result = await asyncio.wait_for(task, timeout=1)
            self.assertEqual(context.close_calls, 1)
            self.assertFalse(result['window_open'])
            self.assertEqual(page.clicks + page.fills, [])
            for value in events:
                self.assertNotIn('body', value)
                self.assertNotIn('screenshots', value)

    async def test_prepare_timeout_keeps_window_and_explicit_check_does_not_prepare_again(self):
        await self.assert_failed_preparation_keeps_window(initial_wait_fails=False)

    async def test_initial_spa_timeout_keeps_window_and_later_check_is_readonly(self):
        await self.assert_failed_preparation_keeps_window(initial_wait_fails=True)


if __name__ == '__main__': unittest.main()
