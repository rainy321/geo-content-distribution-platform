"""A delayed local writer must become editable before filling or publishing."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from services.zhihu_publish_flow import ZhihuEditorNotReadyError
from uploader.zhihu_uploader.main import ZhiHuArticle


FIELDS = ('<textarea placeholder="请输入标题"></textarea>'
          '<div class="public-DraftEditor-content" contenteditable="true" style="height:50px"></div>')


class ZhihuEditorReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        self.article = ZhiHuArticle('说明', '正文', [], 0, 'unused-account.json')

    async def test_waits_for_delayed_title_and_body_without_filling_them(self):
        await self.page.set_content('<main>正在加载编辑器</main>')
        await self.page.evaluate('(html)=>setTimeout(()=>document.body.innerHTML=html,250)', FIELDS)
        await self.article._wait_for_editor_ready(self.page, timeout_seconds=2)
        self.assertEqual(await self.page.get_by_placeholder('请输入标题').input_value(), '')
        self.assertEqual(await self.page.locator('[contenteditable]').inner_text(), '')

    async def test_loading_title_only_hidden_disabled_or_readonly_body_is_not_ready(self):
        for html in ('<main>正在加载编辑器</main>', '<textarea placeholder="请输入标题"></textarea>',
                     '<div hidden>'+FIELDS+'</div>', FIELDS.replace('<textarea ', '<textarea disabled '),
                     FIELDS.replace('contenteditable="true"', 'contenteditable="false"')):
            with self.subTest(html=html):
                await self.page.set_content(html)
                with self.assertRaises(ZhihuEditorNotReadyError):
                    await self.article._wait_for_editor_ready(self.page, timeout_seconds=.1)

    async def test_waits_until_existing_controls_become_enabled_and_editable(self):
        await self.page.set_content(FIELDS.replace('<textarea ', '<textarea disabled ')
                                    .replace('contenteditable="true"', 'contenteditable="false"'))
        await self.page.evaluate('''() => setTimeout(() => {
            document.querySelector('textarea').disabled=false;
            document.querySelector('[contenteditable]').contentEditable='true';
        },250)''')
        await self.article._wait_for_editor_ready(self.page, timeout_seconds=2)
        self.assertTrue(await self.page.get_by_placeholder('请输入标题').is_enabled())
        self.assertTrue(await self.page.locator('[contenteditable]').is_editable())

    async def test_delayed_upload_cannot_fill_before_ready_and_failure_never_publishes(self):
        for ready in (True, False):
            with self.subTest(ready=ready):
                await self.page.set_content('<main>正在加载编辑器</main>')
                page = SimpleNamespace(url='https://zhuanlan.zhihu.com/write', goto=AsyncMock(),
                    locator=self.page.locator)
                context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
                runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
                article = ZhiHuArticle('说明', '正文', [], 0, 'unused-account.json')
                real_wait = article._wait_for_editor_ready
                article._wait_for_editor_ready = lambda p: real_wait(p, timeout_seconds=1 if ready else .1)

                async def title(_page):
                    self.assertTrue(await self.page.get_by_placeholder('请输入标题').is_visible())
                    self.assertTrue(await self.page.locator('[contenteditable]').is_editable())

                article.fill_title = AsyncMock(side_effect=title)
                article.fill_body = AsyncMock()
                article._assert_empty_editor = AsyncMock()
                article._verify_content = AsyncMock()
                article.apply_creation_statement = AsyncMock()
                article.click_publish = AsyncMock()
                if ready:
                    await self.page.evaluate('(html)=>setTimeout(()=>document.body.innerHTML=html,250)', FIELDS)
                    await article.upload(runtime)
                    article.fill_title.assert_awaited_once()
                    article.click_publish.assert_awaited_once()
                else:
                    with self.assertRaises(ZhihuEditorNotReadyError):
                        await article.upload(runtime)
                    article.fill_title.assert_not_awaited()
                    article.apply_creation_statement.assert_not_awaited()
                    article.click_publish.assert_not_awaited()
                context.close.assert_awaited_once()
                browser.close.assert_awaited_once()
