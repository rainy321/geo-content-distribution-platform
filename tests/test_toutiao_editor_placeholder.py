import unittest

from services.toutiao_article_session import read_body


class ToutiaoEmptyEditorDOMTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_placeholder_is_empty_but_real_or_unknown_content_is_preserved(self):
        from playwright.async_api import async_playwright
        widget = '<span class="syl-placeholder ProseMirror-widget" style="position:absolute" ignoreel="true" contenteditable="false">请输入正文</span>'
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.set_content(f'''<div id="empty" class="ProseMirror" contenteditable="true"><p>{widget}<br></p></div>
                    <div id="typed" class="ProseMirror" contenteditable="true"><p>请输入正文</p></div>
                    <div id="draft" class="ProseMirror" contenteditable="true"><p>{widget}<br>已保存的真实内容</p></div>
                    <div id="other" contenteditable="true"><p>{widget}<br></p></div>''')
                self.assertEqual(await read_body(page.locator('#empty')), '')
                self.assertIn('请输入正文', await read_body(page.locator('#typed')))
                self.assertIn('已保存的真实内容', await read_body(page.locator('#draft')))
                self.assertIn('请输入正文', await read_body(page.locator('#other')))
            finally:
                await browser.close()
