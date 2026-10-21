"""Real isolated browser fixtures for preflight gates; no live platform/session."""
import html
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from services.geo_destination_browser import fill_editor


EDITOR_URL = 'https://editor.gates.example.org/editor'
FRAME_URL = 'https://accounts.gates.example.org/requirement'
CONFIG = {'editor_url': EDITOR_URL, 'title_selector': '#title', 'body_selector': '#body',
          'publication_use': 'editorial', 'rule_review': {'state': 'suitable', 'operator_confirmed': True,
              'note': 'Synthetic operator review for isolated browser fixtures.'}}
ARTICLE = {'title': '合成技术文章', 'content': '合成正文，仅用于隔离浏览器回归，不发布到任何平台。'}
BIND_WECHAT = ('终于等到你~\n账号安全提示\n'
              '为了提升账号安全等级，保障每位创作用户的数字资产与创作权益，'
              '请您在发文前，完成微信绑定。\n前往绑定')


def iframe(document, style=''):
    return f'<iframe style="{style}" srcdoc="{html.escape(document, quote=True)}"></iframe>'


class DestinationBrowserGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        paths = [Path(self.playwright.chromium.executable_path)]
        if os.name == 'nt':
            paths += [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                      Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe')]
        executable = next((path for path in paths if path.is_file()), None)
        if executable is None:
            self.skipTest('A local Playwright Chromium or Edge is needed for isolated HTML fixtures')
        self.browser = await self.playwright.chromium.launch(executable_path=str(executable), headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.documents = {}

        async def serve(route):
            document = self.documents.get(route.request.url)
            if document is None:
                await route.abort()
            else:
                await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=document)

        # All pages are synthetic; no external requests or user profiles exist.
        await self.context.route('**/*', serve)

    async def load(self, extra='', frame_document=None):
        self.documents[EDITOR_URL] = ('<!doctype html><html><body>'
            '<input id="title" type="text"><textarea id="body"></textarea>' + extra + '</body></html>')
        if frame_document is not None:
            self.documents[FRAME_URL] = '<!doctype html><html><body>' + frame_document + '</body></html>'
        await self.page.goto(EDITOR_URL, wait_until='load')

    async def assert_no_write(self, message):
        with self.assertRaisesRegex(ValueError, message) as caught:
            await fill_editor(self.page, CONFIG, ARTICLE)
        self.assertEqual(await self.page.locator('#title').input_value(), '')
        self.assertEqual(await self.page.locator('#body').input_value(), '')
        return str(caught.exception)

    async def assert_filled(self):
        result = await fill_editor(self.page, CONFIG, ARTICLE)
        self.assertEqual(result['state'], 'filled')
        self.assertEqual(await self.page.locator('#title').input_value(), ARTICLE['title'])
        self.assertEqual(await self.page.locator('#body').input_value(), ARTICLE['content'])

    async def test_normal_empty_fields_still_fill(self):
        await self.load()
        await self.assert_filled()

    async def test_visible_cross_origin_mandatory_binding_blocks_before_any_write(self):
        await self.load(f'<iframe src="{FRAME_URL}"></iframe>', html.escape(BIND_WECHAT))
        self.assertNotIn('微信绑定', await self.page.locator('body').inner_text())
        self.assertNotEqual(self.page.main_frame.url, self.page.frames[1].url)
        await self.assert_no_write('发文前完成微信绑定')

    async def test_visible_nested_binding_frame_blocks(self):
        await self.load(iframe(iframe(html.escape(BIND_WECHAT))))
        self.assertEqual(len(self.page.frames), 3)
        await self.assert_no_write('微信绑定')

    async def test_mandatory_binding_in_main_document_also_blocks(self):
        await self.load('<aside>' + html.escape(BIND_WECHAT) + '</aside>')
        await self.assert_no_write('微信绑定')

    async def test_mandatory_phone_binding_blocks(self):
        await self.load(iframe('投稿前，请先完成手机号绑定。<button>立即绑定</button>'))
        await self.assert_no_write('手机绑定')

    async def test_binding_required_before_publication_in_reverse_order_blocks(self):
        await self.load(iframe('完成微信绑定后才能发布。<a>前往绑定</a>'))
        await self.assert_no_write('微信绑定')

    async def test_optional_other_binding_does_not_cancel_mandatory_wechat_binding(self):
        await self.load(iframe('发文前无需绑定手机，但必须完成微信绑定。前往绑定'))
        await self.assert_no_write('微信绑定')

    async def test_optional_binding_and_navigation_do_not_block(self):
        for text in ('账号设置 微信绑定 手机绑定',
                     '为了提升账号安全等级，建议绑定微信。前往绑定',
                     '可在发布后自愿完成微信绑定。前往绑定',
                     '发文前无需完成微信绑定，可直接发布。前往绑定'):
            with self.subTest(text=text):
                await self.load(iframe(html.escape(text)))
                await self.assert_filled()

    async def test_hidden_frames_are_not_read_and_do_not_block(self):
        for style in ('display:none', 'visibility:hidden', 'opacity:0',
                      'width:0;height:0;border:0'):
            with self.subTest(style=style):
                await self.load(iframe(html.escape(BIND_WECHAT), style))
                frame = self.page.frames[1]
                with patch.object(frame, 'evaluate', new=AsyncMock(side_effect=RuntimeError('must not read hidden frame'))) as read:
                    await self.assert_filled()
                read.assert_not_awaited()

    async def test_visible_child_inside_hidden_parent_is_not_read(self):
        await self.load(iframe(iframe(html.escape(BIND_WECHAT)), 'display:none'))
        nested = self.page.frames[2]
        with patch.object(nested, 'evaluate', new=AsyncMock(side_effect=RuntimeError('must not read hidden descendant'))) as read:
            await self.assert_filled()
        read.assert_not_awaited()

    async def test_frame_inside_transparent_container_does_not_block(self):
        await self.load('<div style="opacity:0">' + iframe(html.escape(BIND_WECHAT)) + '</div>')
        await self.assert_filled()

    async def test_visible_frame_security_validation_blocks_without_returning_private_text(self):
        secret = 'SYNTHETIC_PRIVATE_ACCOUNT_DO_NOT_RETURN'
        await self.load(iframe('请完成安全验证。' + secret))
        message = await self.assert_no_write('验证、限制')
        self.assertNotIn(secret, message)

    async def test_visible_frame_validation_heading_blocks(self):
        await self.load(iframe('<h1>安全验证</h1><p>请拖动滑块</p>'))
        await self.assert_no_write('验证、限制')

    async def test_access_restriction_in_main_or_visible_frame_blocks(self):
        for extra in ('<p>访问受限</p>', iframe('访问受限')):
            with self.subTest(extra=extra):
                await self.load(extra)
                await self.assert_no_write('验证、限制')

    async def test_visible_frame_read_failure_stops_without_leaking_exception_details(self):
        await self.load(iframe('合成账号页面'))
        frame = self.page.frames[1]
        with patch.object(frame, 'evaluate', new=AsyncMock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_URL_OR_ACCOUNT'))):
            message = await self.assert_no_write('无法读取可见嵌入页面')
        self.assertNotIn('SYNTHETIC_PRIVATE', message)

    async def test_unconfirmed_visible_frame_state_stops(self):
        await self.load(iframe('合成加载页面'))
        frame = self.page.frames[1]
        with patch.object(frame, 'evaluate', new=AsyncMock(return_value={'readable': False})):
            await self.assert_no_write('尚未确认')

    async def test_failed_visibility_observation_stops(self):
        await self.load(iframe('合成账号页面'))
        frame = self.page.frames[1]
        with patch.object(frame, 'frame_element', new=AsyncMock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_FRAME'))):
            message = await self.assert_no_write('无法确认嵌入页面是否可见')
        self.assertNotIn('SYNTHETIC_PRIVATE', message)

    async def test_unrelated_visible_frame_does_not_block(self):
        await self.load(iframe('<nav>账号设置</nav><p>版本说明与帮助</p>'))
        await self.assert_filled()

    async def test_binding_without_a_visible_action_is_not_assumed_mandatory(self):
        await self.load(iframe('此说明介绍了发文前如何完成微信绑定，供读者了解。'))
        await self.assert_filled()


if __name__ == '__main__':
    unittest.main()
