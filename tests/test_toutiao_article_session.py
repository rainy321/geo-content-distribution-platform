import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from services.publisher_adapter import PublishContent, ToutiaoPublisherAdapter
from services import toutiao_article_session as session


class TextField:
    def __init__(self, text='', *, corrupt=False):
        self.text, self.corrupt = text, corrupt
        self.fill = AsyncMock(side_effect=self.write)

    async def write(self, text):
        self.text = '错误截断内容' if self.corrupt else text

    async def input_value(self):
        return self.text

    async def evaluate(self, _expression):
        return self.text


class VisibleArticleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.content = PublishContent(platform='toutiao', title='模型 API 接入核对清单', content='第一段\n\n第二段', tags=('模型接入',))

    async def test_overlong_reviewed_title_stops_before_browser_without_truncation(self):
        factory = Mock()
        with self.assertRaisesRegex(ValueError, '不会自动截断'):
            await session.run_visible_article(PublishContent(platform='toutiao', title='长' * 31, content='正文'), 'synthetic.json', playwright_factory=factory)
        factory.assert_not_called()

    async def test_verification_wait_keeps_same_page_until_normal_clearance(self):
        page = SimpleNamespace(is_closed=lambda: False, goto=AsyncMock())
        browser = SimpleNamespace(is_connected=lambda: True)
        messages = []
        with patch.object(session, 'blocking_reason', new=AsyncMock(side_effect=['需要安全验证', '需要安全验证', None])), \
                patch.object(session.asyncio, 'sleep', new=AsyncMock()) as sleep:
            await session.wait_for_verification(page, browser, messages.append)
        self.assertEqual(sleep.await_count, 2)
        page.goto.assert_not_called()
        self.assertEqual(len(messages), 2)
        self.assertIn('当前发布窗口', messages[0])

    async def test_closing_visible_window_cancels_manual_wait(self):
        page = SimpleNamespace(is_closed=lambda: True)
        with self.assertRaises(session.ArticleSessionStopped):
            await session.wait_for_verification(page, SimpleNamespace(is_connected=lambda: False), None)

    async def test_readback_requires_complete_title_and_body_not_just_length(self):
        for actual_title, actual_body in [('错标题', '第一段\n第二段'), (self.content.title, '第一段\n被替换')]:
            with self.assertRaises(session.ArticleSessionStopped):
                await session.verify_readback((TextField(actual_title), TextField(actual_body)), self.content.title, self.content.content)
        await session.verify_readback((TextField(self.content.title), TextField('第一段\r\n第二段')), self.content.title, self.content.content)

    async def execute_isolated(self, *, corrupt=False, existing=False, mutate_after_fill=False,
                               close_before=False, interruption_after=None, permissions_error=False, sync_dialog=False):
        title, body = TextField('已有草稿' if existing else ''), TextField(corrupt=corrupt)
        page = SimpleNamespace(url='https://mp.toutiao.com/profile_v4/graphic/publish', goto=AsyncMock(), is_closed=lambda: False)
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), storage_state=AsyncMock(), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), is_connected=lambda: True, close=AsyncMock())
        launch = AsyncMock(return_value=browser)
        runtime = MagicMock()
        runtime.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        helper = SimpleNamespace(handle_cover=AsyncMock(), apply_work_statements=AsyncMock())
        if mutate_after_fill:
            async def change(_page):
                body.text = '编辑器意外改变了正文'
            helper.handle_cover.side_effect = change
        async def submit_once(_page, **callbacks):
            await callbacks['on_submission_attempt']()
            if interruption_after == 'close':
                page.is_closed = lambda: True
                raise RuntimeError('synthetic page closed')
            if interruption_after == 'cancel':
                raise asyncio.CancelledError()
            if interruption_after == 'error':
                raise RuntimeError('synthetic submission transport error')
            await callbacks['after_submission']()
            return {'status': 'processing', 'success': False, 'message': '已提交，等待平台审核'}
        submit = AsyncMock(side_effect=submit_once)
        messages = []
        records = []
        with patch.object(session, 'editor_fields', new=AsyncMock(return_value=(title, body))), \
                patch.object(session, 'wait_for_verification', new=AsyncMock(side_effect=session.ArticleSessionStopped('关闭提交前窗口') if close_before else None)), \
                patch.object(session, 'ai_declaration_selected', new=AsyncMock(return_value=True)), \
                patch.object(session, 'enforce_article_permissions', new=AsyncMock(side_effect=session.ArticleSessionStopped('声明状态不明确') if permissions_error else None, return_value={'ai_reference':True,'first_publication':False})), \
                patch.object(session, 'decline_observed_sync_authorization', new=AsyncMock(return_value=sync_dialog)), \
                patch('uploader.toutiao_uploader.main.TouTiaoArticle', return_value=helper), \
                patch('services.toutiao_publish_flow.click_exact_publish_and_observe', submit):
            result = await session.run_visible_article(self.content, 'synthetic.json', messages.append,
                playwright_factory=lambda: runtime, record_submission=records.append)
        self.assertFalse(launch.await_args.kwargs['headless'])
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        return result, submit, title, body, messages, records

    async def test_reviewed_text_is_filled_and_checked_before_single_submission(self):
        result, submit, title, body, messages, records = await self.execute_isolated()
        self.assertEqual(result['status'], 'processing')
        submit.assert_awaited_once()
        self.assertEqual(title.text, self.content.title)
        self.assertEqual(body.text, '第一段\n\n第二段\n\n#模型接入')
        self.assertTrue(any('回读核对通过' in text for text in messages))
        self.assertTrue(result['submission_attempted'])
        self.assertEqual(records[-1]['submission_phase'], 'result_observed')

    async def test_failed_body_write_never_submits(self):
        result, submit, *_ = await self.execute_isolated(corrupt=True)
        self.assertEqual(result['status'], 'need_action')
        self.assertIn('正文回读', result['message'])
        submit.assert_not_awaited()

    async def test_existing_remote_draft_is_preserved_without_submission(self):
        result, submit, title, body, *_ = await self.execute_isolated(existing=True)
        self.assertEqual(result['status'], 'need_action')
        title.fill.assert_not_awaited()
        body.fill.assert_not_awaited()
        submit.assert_not_awaited()

    async def test_late_editor_changes_stop_submission_after_cover_and_declaration(self):
        result, submit, *_ = await self.execute_isolated(mutate_after_fill=True)
        self.assertEqual(result['status'], 'need_action')
        submit.assert_not_awaited()

    async def test_pre_submission_close_remains_need_action_without_attempt(self):
        result, submit, *_ = await self.execute_isolated(close_before=True)
        self.assertEqual(result['status'], 'need_action')
        self.assertFalse(result['submission_attempted'])
        self.assertEqual(result['submission_phase'], 'stopped_before_submission')
        submit.assert_not_awaited()

    async def test_close_or_verification_cancel_after_click_remains_processing(self):
        for interruption in ('close', 'cancel'):
            with self.subTest(interruption=interruption):
                result, submit, _, _, _, records = await self.execute_isolated(interruption_after=interruption)
                self.assertEqual(result['status'], 'processing')
                self.assertTrue(result['submission_attempted'])
                self.assertEqual(records[-1]['submission_phase'], 'submission_result_unknown')
                self.assertIn('最终状态未知', result['message'])
                submit.assert_awaited_once()

    async def test_submission_transport_error_cannot_become_retryable_failure(self):
        result, submit, *_ = await self.execute_isolated(interruption_after='error')
        self.assertEqual(result['status'], 'processing')
        self.assertTrue(result['submission_attempted'])
        self.assertIn('不会自动重发', result['message'])
        submit.assert_awaited_once()

    async def test_unverifiable_extra_declarations_stop_before_any_article_write(self):
        result, submit, title, body, *_ = await self.execute_isolated(permissions_error=True)
        self.assertEqual(result['status'],'need_action')
        self.assertFalse(result['content_write_attempted'])
        title.fill.assert_not_awaited()
        body.fill.assert_not_awaited()
        submit.assert_not_awaited()

    async def test_post_submission_sync_cancel_preserves_processing_and_never_resubmits(self):
        result, submit, _, _, _, records = await self.execute_isolated(sync_dialog=True)
        self.assertEqual(result['status'],'processing')
        self.assertEqual(result['cross_product_sync_authorization'],'declined')
        self.assertIn('cross_product_sync_authorization_declined',[r['submission_phase'] for r in records])
        submit.assert_awaited_once()


class InteractiveAdapterTests(unittest.TestCase):
    def test_desktop_manual_wait_is_not_cut_off_by_legacy_total_timeout(self):
        async def slow_session(_content, _file):
            await asyncio.sleep(0.025)
            return {'status': 'need_action', 'message': '测试正常人工交接'}
        checker = Mock(side_effect=AssertionError('must inspect the visible page instead'))
        adapter = ToutiaoPublisherAdapter('synthetic.json', timeout_seconds=0.001,
            interactive=True, login_checker=checker, publish_runner=slow_session)
        result = adapter.publish(PublishContent(platform='toutiao', title='标题', content='正文'))
        self.assertEqual(result.status, 'need_action')
        self.assertIn('人工交接', result.message)
        checker.assert_not_called()


class LocalEditorBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.runtime = await async_playwright().start()
        self.browser = await self.runtime.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={'width': 1200, 'height': 800})
        await self.page.set_content('<input placeholder="文章标题"><div contenteditable="true"></div><label><input type="radio" id="ai">引用AI</label><div id="captcha-test" style="display:none;width:200px;height:120px">请完成安全验证</div>')

    async def asyncTearDown(self):
        await self.browser.close()
        await self.runtime.stop()

    async def test_real_contenteditable_readback_and_later_corruption(self):
        fields = await session.editor_fields(self.page)
        await fields[0].fill('真实本地编辑器验收')
        await fields[1].fill('第一段\n\n第二段')
        await session.verify_readback(fields, '真实本地编辑器验收', '第一段\n第二段')
        await fields[1].fill('第一段\n正文已改变')
        with self.assertRaises(session.ArticleSessionStopped):
            await session.verify_readback(fields, '真实本地编辑器验收', '第一段\n第二段')

    async def test_ai_declaration_requires_checked_state(self):
        self.assertFalse(await session.ai_declaration_selected(self.page))
        await self.page.locator('#ai').check()
        self.assertTrue(await session.ai_declaration_selected(self.page))

    async def test_hidden_or_offscreen_captcha_does_not_block_editor(self):
        self.assertIsNone(await session.blocking_reason(self.page))
        await self.page.locator('#captcha-test').evaluate("el => el.style.display='block'")
        self.assertEqual(await session.blocking_reason(self.page), '需要安全验证')
        await self.page.locator('#captcha-test').evaluate("el => { el.style.position='absolute'; el.style.top='2000px' }")
        self.assertIsNone(await session.blocking_reason(self.page))

    async def permissions_fixture(self, extra=''):
        # This is a semantic control fixture using observed screenshot labels,
        # not a claim that today's logged-in platform DOM was captured.
        await self.page.set_content('''
          <label>声明首发</label><label><input id="first" type="checkbox" checked>头条首发</label>
          <label><input id="rights" type="checkbox" checked>授权平台自动维权</label>
          <label><input id="original" type="checkbox" checked>声明原创</label>
          <div>同时发布微头条</div><label><input id="micro" type="checkbox" checked>发布得更多收益</label>
          <label><input id="advert" type="radio" name="ads" checked>投放广告赚收益</label>
          <label><input id="no-advert" type="radio" name="ads">不投放广告</label>
          <label><input id="donate" type="checkbox" checked>允许赞赏（今日还有5次机会）</label>
          <label><input id="ai" type="checkbox">引用AI</label>''' + extra)

    async def test_exact_checked_controls_disable_extra_grants_and_keep_ai_reference(self):
        await self.permissions_fixture()
        audited = await session.enforce_article_permissions(self.page)
        self.assertTrue(audited['ai_reference'])
        for name in ('first','rights','original','micro','advert','donate'):
            self.assertFalse(await self.page.locator('#'+name).is_checked())
        self.assertTrue(await self.page.locator('#no-advert').is_checked())
        self.assertTrue(await self.page.locator('#ai').is_checked())
        # Rechecking does not toggle the now-safe declarations back on.
        await session.enforce_article_permissions(self.page)
        self.assertTrue(await self.page.locator('#ai').is_checked())

    async def test_css_checked_class_is_not_authorization_state_and_duplicates_stop(self):
        await self.permissions_fixture()
        await self.page.locator('#first').evaluate("el=>el.replaceWith(Object.assign(document.createElement('span'),{className:'byte-checkbox-checked'}))")
        with self.assertRaises(session.ArticleSessionStopped):
            await session.enforce_article_permissions(self.page)
        await self.permissions_fixture('<label><input type="checkbox">头条首发</label>')
        with self.assertRaises(session.ArticleSessionStopped):
            await session.enforce_article_permissions(self.page)

    async def test_unknown_or_disabled_extra_declaration_stops_for_manual_review(self):
        await self.permissions_fixture('<label><input type="checkbox" checked>原创及转载同步授权</label>')
        with self.assertRaises(session.ArticleSessionStopped):
            await session.enforce_article_permissions(self.page)
        await self.permissions_fixture()
        await self.page.locator('#first').evaluate('el=>el.disabled=true')
        with self.assertRaises(session.ArticleSessionStopped):
            await session.enforce_article_permissions(self.page)

    async def test_hidden_unbound_or_unknown_checked_grants_never_become_assumed_false(self):
        extras = (
            '<label style="display:none"><input type="checkbox" checked>原创</label>',
            '<div role="checkbox" aria-checked="true" aria-label="授权平台自动维权"></div>',
            '<input type="checkbox" checked>',
            '<label><input type="checkbox" checked>未知授权</label>',
            '<label><span role="checkbox" aria-checked="mixed">原创</span></label>',
        )
        for extra in extras:
            with self.subTest(extra=extra):
                await self.permissions_fixture(extra)
                await self.page.evaluate("window.anyClick=false;document.addEventListener('click',()=>window.anyClick=true)")
                with self.assertRaises(session.ArticleSessionStopped):
                    await session.enforce_article_permissions(self.page)
                self.assertFalse(await self.page.evaluate('window.anyClick'), 'Unknown grants must stop before guessing a control to click')

    async def test_hidden_unchecked_grant_is_read_as_false_without_clicking_it(self):
        await self.permissions_fixture('<label style="display:none"><input id="hidden-grant" type="checkbox">原创</label>')
        await session.enforce_article_permissions(self.page)
        self.assertFalse(await self.page.locator('#hidden-grant').is_checked())

    async def test_observed_sync_dialog_only_cancels_and_never_accepts(self):
        await self.page.set_content('''<div role="dialog"><h2>作品同步授权</h2>
          <p>开启作品同步后，本次及未来在今日头条和西瓜视频发布的公开作品将同步至【懂车帝】和【住小帮】。</p>
          <p>如果你不希望自己的作品同步到上述平台，可以点击取消。</p>
          <button onclick="window.cancelled=true;this.closest('[role=dialog]').remove()">取消</button>
          <button onclick="window.accepted=true">确定</button></div>''')
        self.assertTrue(await session.decline_observed_sync_authorization(self.page))
        self.assertTrue(await self.page.evaluate('window.cancelled'))
        self.assertFalse(await self.page.evaluate('Boolean(window.accepted)'))

    async def test_unverified_sync_dialog_structure_never_clicks_any_confirmation(self):
        for markup in ('''<div><h2>作品同步授权</h2><button>取消</button><button>确定</button></div>''',
                       '''<div role="dialog"><h2>作品同步授权</h2><p>新的授权内容</p><button>取消</button><button>确定</button></div>'''):
            await self.page.set_content(markup)
            await self.page.evaluate("document.addEventListener('click',()=>window.anyClick=true)")
            with self.assertRaises(session.ArticleSessionStopped):
                await session.decline_observed_sync_authorization(self.page)
            self.assertFalse(await self.page.evaluate('Boolean(window.anyClick)'))
