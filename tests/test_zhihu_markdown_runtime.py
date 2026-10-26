"""Ordinary local file-chooser events, without accounts or platform access."""
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from services.zhihu_markdown_editor import IMPORT_ACCEPT, build_markdown_manifest, import_markdown_body
from services.zhihu_publish_flow import ZhihuEditorNotReadyError
from uploader.zhihu_uploader.main import ZhiHuArticle


TITLE = '示例采购说明'
BODY = '第一段保留  两个空格。\n\n## 采购边界\n\n查看[公开说明](https://example.invalid/about/)。'
RENDERED = '''<div data-contents="true">
<div class="Editable-unstyled" data-block="true"><span data-text="true">第一段保留  两个空格。</span></div>
<h3 data-block="true"><span data-text="true">采购边界</span></h3>
<div class="Editable-unstyled" data-block="true"><span data-text="true">查看</span><a data-editable="true" href="https://example.invalid/about/"><span data-text="true">公开说明</span></a><span data-text="true">。</span></div>
</div>'''


class LocalWriter:
    def __init__(self, page):
        self._page = page
        self.url = 'https://zhuanlan.zhihu.com/write'

    def __getattr__(self, name):
        return getattr(self._page, name)


class ZhihuMarkdownRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.writer = LocalWriter(self.page)
        html = '''<!doctype html><meta charset="utf-8">
<textarea placeholder="请输入标题"></textarea>
<div class="public-DraftEditor-content" contenteditable="true" style="min-height:80px"><div data-contents="true"><div class="Editable-unstyled" data-block="true"><br></div></div></div>
<button aria-label="导入" id="trigger">导入</button>
<button id="document" hidden>导入文档</button>
<div id="modal" hidden><button id="upload">点击选择本地文档或拖动文件到窗口上传</button>
<input id="file" type="file" style="display:none" accept="ACCEPT"></div>
<script>
(() => {
window.imports=[];window.clicks=[];
const file=document.querySelector('#file');
document.querySelector('#trigger').onclick=()=>{window.clicks.push('import');document.querySelector('#document').hidden=false;};
document.querySelector('#document').onclick=()=>{window.clicks.push('document');document.querySelector('#modal').hidden=false;};
document.querySelector('#upload').onclick=()=>{window.clicks.push('upload');file.click();};
file.onchange=async()=>{
  const text=await file.files[0].text();window.imports.push({name:file.files[0].name,text});
  document.querySelector('.public-DraftEditor-content').innerHTML=window.rendered;
  document.querySelector('#modal').hidden=true;
};
})();
</script>'''.replace('ACCEPT', IMPORT_ACCEPT)
        await self.page.set_content(html)
        await self.page.locator('textarea').fill(TITLE)
        await self.page.evaluate('(html)=>{window.rendered=html;}', RENDERED)
        self.editor = self.page.locator('.public-DraftEditor-content')
        self.manifest = build_markdown_manifest(BODY)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def importing(self):
        await import_markdown_body(self.writer, self.editor, markdown=BODY, expected_title=TITLE,
                                   manifest=self.manifest, wait_seconds=.25)

    async def test_real_chooser_chain_imports_body_once_and_preserves_title(self):
        await self.importing()
        self.assertEqual(await self.page.evaluate('window.clicks'), ['import', 'document', 'upload'])
        uploads = await self.page.evaluate('window.imports')
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]['text'], BODY)
        self.assertNotIn('# '+TITLE, uploads[0]['text'])
        self.assertEqual(await self.page.locator('textarea').input_value(), TITLE)

    async def test_existing_body_is_never_replaced_or_opened_for_import(self):
        await self.editor.fill('原有草稿必须保留')
        with self.assertRaises(ZhihuEditorNotReadyError) as caught:
            await self.importing()
        self.assertEqual(caught.exception.reason_code, 'editor_existing_content')
        self.assertEqual(await self.editor.inner_text(), '原有草稿必须保留')
        self.assertEqual(await self.page.evaluate('window.clicks'), [])

    async def test_unexpected_or_ambiguous_chooser_controls_stop_before_upload(self):
        for action in (
                "document.querySelector('#file').accept='.md'",
                "document.querySelector('#file').multiple=true",
                "document.querySelector('#file').after(document.querySelector('#file').cloneNode())"):
            await self.page.evaluate(action)
            with self.assertRaises(ZhihuEditorNotReadyError) as caught:
                await self.importing()
            self.assertEqual(caught.exception.reason_code, 'markdown_import_controls_unconfirmed')
            self.assertEqual(await self.page.evaluate('window.imports'), [])
            # One fresh fixture per case keeps the same ordinary UI sequence.
            if action != "document.querySelector('#file').after(document.querySelector('#file').cloneNode())":
                await self.asyncTearDown()
                await self.asyncSetUp()

    async def test_title_change_before_file_selection_prevents_import(self):
        await self.page.evaluate("document.querySelector('#document').addEventListener('click',()=>{document.querySelector('textarea').value='另一篇标题';})")
        with self.assertRaises(ZhihuEditorNotReadyError) as caught:
            await self.importing()
        self.assertEqual(caught.exception.reason_code, 'markdown_import_unconfirmed')
        self.assertEqual(await self.page.evaluate('window.imports'), [])

    async def test_same_length_content_corruption_never_counts_as_success_or_replays(self):
        await self.page.evaluate('(html)=>{window.rendered=html;}', RENDERED.replace('采购边界', '采购广告'))
        with self.assertRaises(ZhihuEditorNotReadyError) as caught:
            await self.importing()
        self.assertEqual(caught.exception.reason_code, 'markdown_semantic_mismatch')
        self.assertEqual(len(await self.page.evaluate('window.imports')), 1)

    async def test_exact_title_and_semantics_are_checked_again_after_statement(self):
        await self.importing()
        article = ZhiHuArticle(TITLE, BODY, [], 0, 'unused.json', content_format='markdown')
        await article._verify_content(self.writer)
        await self.page.locator('a').evaluate("a=>a.setAttribute('href','https://other.invalid/about/')")
        with self.assertRaises(ZhihuEditorNotReadyError) as caught:
            await article._verify_content(self.writer)
        self.assertEqual(caught.exception.reason_code, 'markdown_semantic_mismatch')

    async def test_same_uploader_never_repeats_uncertain_import(self):
        article = ZhiHuArticle(TITLE, BODY, [], 0, 'unused.json', content_format='markdown')
        with patch('services.zhihu_markdown_editor.import_markdown_body',
                                 new=AsyncMock(side_effect=ZhihuEditorNotReadyError(
                                     'unconfirmed', reason_code='markdown_import_unconfirmed'))) as importer:
            for _ in range(2):
                with self.assertRaises(ZhihuEditorNotReadyError):
                    await article.fill_body(self.writer)
            importer.assert_awaited_once()

    async def test_other_origin_never_opens_import(self):
        self.writer.url = 'https://other.invalid/write'
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.importing()
        self.assertEqual(await self.page.evaluate('window.clicks'), [])
