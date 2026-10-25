"""Fail-closed AI statement checks using local editor DOM; no live account or posting."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from uploader.zhihu_uploader.main import ZhiHuArticle, ZHIHU_AI_CREATION_STATEMENT


AI = ZHIHU_AI_CREATION_STATEMENT


def row(current='无声明', *, control_attributes='', action='window.combos++', extra=''):
    return ('<div class="statement"><label>创作声明</label>'
            f'<button role="combobox" {control_attributes} onclick="{action}">{current}</button>'
            f'{extra}</div>')


def option(*, attributes='', action='document.querySelector("[role=combobox]").textContent=window.ai'):
    return f'<button role="option" {attributes} onclick=\'{action}\'>{AI}</button>'


class ZhihuAIStatementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        self.browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.article = ZhiHuArticle('选型说明', '有依据的正文', [], 0, 'unused-account.json',
                                    creation_statement=AI)

    async def prepare(self, html):
        await self.page.set_content('<meta charset="utf-8"><script>window.combos=0;</script>'
            '<textarea placeholder="请输入标题"></textarea>'
            '<div class="public-DraftEditor-content" contenteditable="true" style="height:50px"></div>' + html)
        await self.page.evaluate('(label)=>window.ai=label', AI)

    async def upload_with_local_dom(self):
        # Exercise the real upload sequencing while supplying local DOM for
        # the statement and replacing all account/network/browser operations.
        page = SimpleNamespace(url='https://zhuanlan.zhihu.com/write',
            goto=AsyncMock(), wait_for_timeout=AsyncMock(), locator=self.page.locator,
            get_by_role=self.page.get_by_role)
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(),
                                  add_init_script=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        self.article.fill_title = AsyncMock()
        self.article.fill_body = AsyncMock()
        self.article.click_publish = AsyncMock()
        await self.article.upload(runtime)
        context.add_init_script.assert_not_awaited()
        return page

    async def test_already_selected_exact_ai_statement_needs_no_selection_click(self):
        await self.prepare(row(AI.replace(' ', '\n')))
        await self.upload_with_local_dom()
        self.assertEqual(await self.page.evaluate('window.combos'), 0)
        self.article.click_publish.assert_awaited_once()

    async def test_selection_waits_for_visible_exact_readback_before_publish(self):
        await self.prepare(row() + option(action='setTimeout(()=>document.querySelector("[role=combobox]").textContent=window.ai,150)'))
        await self.upload_with_local_dom()
        self.assertEqual(await self.page.get_by_role('combobox').inner_text(), AI)
        self.article.click_publish.assert_awaited_once()

    async def test_absent_hidden_ambiguous_or_disabled_controls_prevent_publish(self):
        for html in ('<div>没有创作声明设置</div>', '<div hidden>'+row()+'</div>', row()+row(),
                     '<div><label>创作声明</label></div>', row(extra='<button role="combobox">无声明</button>'),
                     row(control_attributes='disabled'), row(control_attributes='aria-disabled="true"')):
            with self.subTest(html=html):
                await self.prepare(html)
                with self.assertRaisesRegex(RuntimeError, 'AI 创作声明尚未确认.*人工'):
                    await self.upload_with_local_dom()
                self.article.click_publish.assert_not_awaited()

    async def test_missing_hidden_disabled_or_ambiguous_option_prevents_publish(self):
        for choices in ('', option(attributes='hidden'), option(attributes='disabled'), option()+option()):
            with self.subTest(choices=choices):
                await self.prepare(row()+choices)
                with self.assertRaisesRegex(RuntimeError, 'AI 创作声明尚未确认.*人工'):
                    await self.upload_with_local_dom()
                self.article.click_publish.assert_not_awaited()
                self.assertEqual(await self.page.get_by_role('combobox').inner_text(), '无声明')

    async def test_option_click_without_updated_readback_prevents_publish(self):
        await self.prepare(row()+option(action='window.optionClicked=true'))
        with self.assertRaisesRegex(RuntimeError, 'AI 创作声明尚未确认.*人工'):
            await self.upload_with_local_dom()
        self.assertTrue(await self.page.evaluate('window.optionClicked'))
        self.article.click_publish.assert_not_awaited()

    async def test_ambiguous_or_disabled_result_after_selection_prevents_publish(self):
        for tail in ('document.querySelector("[role=combobox]").disabled=true',
                     'document.querySelector(".statement").insertAdjacentHTML("beforeend", "<button role=combobox>"+window.ai+"</button>")'):
            with self.subTest(tail=tail):
                await self.prepare(row()+option(action='document.querySelector("[role=combobox]").textContent=window.ai;'+tail))
                with self.assertRaisesRegex(RuntimeError, 'AI 创作声明尚未确认.*人工'):
                    await self.upload_with_local_dom()
                self.article.click_publish.assert_not_awaited()

    async def test_legacy_explicit_no_statement_choice_is_unchanged(self):
        article = ZhiHuArticle('普通稿件', '正文', [], 0, 'unused-account.json', creation_statement='无声明')
        await self.prepare(row())
        await article.apply_creation_statement(self.page)
        self.assertEqual(await self.page.get_by_role('combobox').inner_text(), '无声明')


if __name__ == '__main__':
    unittest.main()
