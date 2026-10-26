"""Semantic checks against local replicas of the observed native-import DOM."""

import copy
import html
import unittest

from playwright.async_api import async_playwright

from services.zhihu_markdown_editor import (
    build_markdown_manifest,
    read_markdown_document,
    verify_markdown_document,
)
from services.zhihu_publish_flow import ZhihuEditorNotReadyError


# Generic fixture data: this module contains no real article, account or profile.
MARKDOWN = """# 产品与服务边界

示例组织提供接口、客户端和集成服务。

查看[接口说明](https://example.invalid/api/)与[客户端说明](https://example.invalid/client/)。

## 对比表

| 类型 | 适用场景 | 交付范围 |
| --- | --- | --- |
| 接口 | 开发集成 | 调用入口 |
| 客户端 | 桌面使用 | 操作界面 |
| 服务 | 业务实施 | 项目交付 |

参考[服务说明](https://example.invalid/services/)及[主体说明](https://example.invalid/about/)。

本文只说明范围，实际交付以双方确认内容为准。"""

EXPECTED = [
    {"type": "heading", "level": 1, "text": "产品与服务边界", "links": []},
    {"type": "paragraph", "text": "示例组织提供接口、客户端和集成服务。", "links": []},
    {
        "type": "paragraph",
        "text": "查看接口说明与客户端说明。",
        "links": [
            {"text": "接口说明", "href": "https://example.invalid/api/"},
            {"text": "客户端说明", "href": "https://example.invalid/client/"},
        ],
    },
    {"type": "heading", "level": 2, "text": "对比表", "links": []},
    {
        "type": "table",
        "rows": [
            ["类型", "适用场景", "交付范围"],
            ["接口", "开发集成", "调用入口"],
            ["客户端", "桌面使用", "操作界面"],
            ["服务", "业务实施", "项目交付"],
        ],
    },
    {
        "type": "paragraph",
        "text": "参考服务说明及主体说明。",
        "links": [
            {"text": "服务说明", "href": "https://example.invalid/services/"},
            {"text": "主体说明", "href": "https://example.invalid/about/"},
        ],
    },
    {"type": "paragraph", "text": "本文只说明范围，实际交付以双方确认内容为准。", "links": []},
]


def _text(value):
    return '<span><span data-text="true">' + html.escape(value) + '</span></span>'


def _link(label, href, *, icon=True):
    decoration = (
        '<span style="display: inline-flex; align-items: center;">\u200b'
        '<svg class="ZDI ZDI--Link24" style="margin-right: 4px; vertical-align: text-bottom;">'
        '<path d="M1 1L2 2"></path></svg></span>'
        if icon else ''
    )
    return (
        '<a data-editable="true" class="Link ztext-link" target="_blank" '
        f'href="{html.escape(href, quote=True)}">{decoration}{_text(label)}</a>'
    )


def _block(tag, contents, *, index):
    class_name = 'Editable-unstyled' if tag == 'div' else 'Editable-styled'
    return (
        f'<{tag} class="{class_name}" data-block="true" data-offset-key="fixture-{index}-0">'
        '<div class="public-DraftStyleDefault-block public-DraftStyleDefault-ltr">'
        f'{contents}</div></{tag}>'
    )


def document_html(*, icons=True):
    rows = []
    for row_index, row in enumerate(EXPECTED[4]['rows']):
        cell_tag = 'th' if row_index == 0 else 'td'
        cells = ''.join(
            f'<{cell_tag} class="Table-data">'
            '<div class="Table-dataInputContainer">'
            f'<div class="Table-dataInput" contenteditable="true">{html.escape(value)}</div>'
            f'</div></{cell_tag}>'
            for value in row
        )
        rows.append(f'<tr class="Table-row">{cells}</tr>')
    table = (
        '<figure class="Editable-styled" data-block="true" contenteditable="false">'
        '<div class="TableToolbar-container"><div>'
        '<table class="Table FocusPlugin--unfocused" data-draft-type="table"><tbody>'
        + ''.join(rows) + '</tbody></table></div></div></figure>'
    )
    blocks = [
        _block('h2', _text(EXPECTED[0]['text']), index=0),
        _block('div', _text(EXPECTED[1]['text']), index=1),
        _block('div', _text('查看')
               + _link('接口说明', 'https://example.invalid/api/', icon=icons)
               + _text('与')
               + _link('客户端说明', 'https://example.invalid/client/', icon=icons)
               + _text('。'), index=2),
        _block('h3', _text(EXPECTED[3]['text']), index=3),
        table,
        _block('div', _text('参考')
               + _link('服务说明', 'https://example.invalid/services/', icon=icons)
               + _text('及')
               + _link('主体说明', 'https://example.invalid/about/', icon=icons)
               + _text('。'), index=5),
        _block('div', _text(EXPECTED[6]['text']), index=6),
    ]
    return (
        '<!doctype html><meta charset="utf-8">'
        '<div class="notranslate public-DraftEditor-content" contenteditable="true" role="textbox">'
        '<div data-contents="true">' + ''.join(blocks) + '</div></div>'
    )


class MarkdownManifestTests(unittest.TestCase):
    def test_source_manifest_has_explicit_ordered_content_links_and_table_cells(self):
        self.assertEqual(build_markdown_manifest(MARKDOWN), EXPECTED)

    def test_unsupported_source_is_rejected_instead_of_silently_losing_content(self):
        for source in (
            '```python\nprint("sample")\n```',
            '![示例图](https://example.invalid/picture.png)',
            '### 不在已支持层级中的标题',
            '- 未支持的列表项',
            '> 未支持的引用块',
            '[带凭据的链接](https://name:secret@example.invalid/path/)',
            '| 列一 | 列二 |\n| --- | --- |\n| 缺少一列 |',
            '| 列一 |\n| --- |\n| [表格链接](https://example.invalid/) |',
        ):
            with self.subTest(source=source):
                with self.assertRaises(ZhihuEditorNotReadyError) as raised:
                    build_markdown_manifest(source)
                self.assertEqual(raised.exception.reason_code, 'markdown_unsupported')

    def test_empty_source_is_rejected_with_explicit_typed_reason(self):
        for source in ('', ' ', '\r\n\t\r\n'):
            with self.subTest(source=source):
                with self.assertRaises(ZhihuEditorNotReadyError) as raised:
                    build_markdown_manifest(source)
                self.assertEqual(raised.exception.reason_code, 'markdown_unsupported')


class NativeMarkdownDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.reset()

    async def reset(self, *, icons=True):
        await self.page.set_content(document_html(icons=icons))
        self.editor = self.page.locator('.public-DraftEditor-content')

    async def assert_document_rejected(self):
        with self.assertRaises(ZhihuEditorNotReadyError) as raised:
            await verify_markdown_document(self.editor, copy.deepcopy(EXPECTED))
        self.assertEqual(raised.exception.reason_code, 'markdown_semantic_mismatch')

    async def test_observed_native_dom_reads_and_verifies_full_semantic_document(self):
        self.assertEqual(await read_markdown_document(self.editor), EXPECTED)
        await verify_markdown_document(self.editor, build_markdown_manifest(MARKDOWN))
        self.assertEqual(await self.editor.locator('a').count(), 4)
        self.assertEqual(await self.editor.locator('table tr').count(), 4)

    async def test_links_without_optional_icon_decoration_still_preserve_exact_labels(self):
        await self.reset(icons=False)
        self.assertEqual(await read_markdown_document(self.editor), EXPECTED)
        await verify_markdown_document(self.editor, EXPECTED)

    async def test_same_link_count_and_label_cannot_hide_changed_destination(self):
        await self.editor.locator('a').first.evaluate(
            "e => e.href = 'https://wrong.invalid/api/'")
        self.assertEqual(await self.editor.locator('a').count(), 4)
        await self.assert_document_rejected()

    async def test_missing_duplicate_or_reordered_links_are_rejected(self):
        mutations = (
            "e => e.querySelector('a').remove()",
            "e => {const a=e.querySelector('a');a.after(a.cloneNode(true));}",
            "e => {const a=e.querySelectorAll('a');"
            "const first=a[0].href;a[0].href=a[1].href;a[1].href=first;}",
        )
        for script in mutations:
            with self.subTest(mutation=script):
                await self.reset()
                await self.editor.evaluate(script)
                await self.assert_document_rejected()

    async def test_same_table_shape_with_swapped_cell_content_is_rejected(self):
        await self.editor.locator('table').evaluate('''table => {
            const cells=table.querySelectorAll('.Table-dataInput');
            const first=cells[3].textContent;
            cells[3].textContent=cells[4].textContent;cells[4].textContent=first;
        }''')
        self.assertEqual(await self.editor.locator('table tr').count(), 4)
        self.assertEqual(await self.editor.locator('table .Table-dataInput').count(), 12)
        await self.assert_document_rejected()

    async def test_table_row_column_and_span_changes_are_rejected(self):
        mutations = (
            "t => t.querySelector('tr').remove()",
            "t => {const r=t.querySelector('tr');r.after(r.cloneNode(true));}",
            "t => t.querySelector('tr').lastElementChild.remove()",
            "t => t.querySelector('th').setAttribute('colspan','2')",
            "t => t.querySelector('td').setAttribute('rowspan','2')",
        )
        for script in mutations:
            with self.subTest(mutation=script):
                await self.reset()
                await self.editor.locator('table').evaluate(script)
                await self.assert_document_rejected()

    async def test_extra_or_duplicate_paragraph_table_or_body_title_is_rejected(self):
        extras = (
            _block('div', _text('额外正文'), index=90),
            _block('h1', _text('额外的文章标题'), index=91),
            _block('h2', _text('由文首 Markdown 标题产生的重复文章标题'), index=92),
        )
        for fragment in extras:
            with self.subTest(fragment=fragment):
                await self.reset()
                await self.editor.locator('[data-contents=true]').evaluate(
                    '(e, fragment) => e.insertAdjacentHTML("beforeend", fragment)', fragment)
                await self.assert_document_rejected()
        for selector in ('div.Editable-unstyled', 'figure'):
            with self.subTest(duplicate=selector):
                await self.reset()
                await self.editor.locator(selector).first.evaluate(
                    'e => e.after(e.cloneNode(true))')
                await self.assert_document_rejected()

    async def test_hidden_blocks_or_inline_payload_cannot_be_ignored(self):
        mutations = (
            "e => e.querySelector('[data-contents]').insertAdjacentHTML('beforeend',"
            "'<div data-block=\"true\" class=\"Editable-unstyled\" style=\"display:none\">隐藏正文</div>')",
            "e => e.querySelector('div.Editable-unstyled').insertAdjacentHTML('beforeend',"
            "'<span style=\"display:none\">隐藏污染</span>')",
            "e => e.querySelector('div.Editable-unstyled').insertAdjacentHTML('beforeend',"
            "'<span style=\"visibility:hidden\">隐藏污染</span>')",
        )
        for script in mutations:
            with self.subTest(mutation=script):
                await self.reset()
                await self.editor.evaluate(script)
                await self.assert_document_rejected()

    async def test_unaccounted_text_inside_or_outside_content_wrapper_is_rejected(self):
        mutations = (
            "e => e.prepend(document.createTextNode('块外前置污染'))",
            "e => e.append(document.createTextNode('块外尾部污染'))",
            "e => e.querySelector('[data-contents]').append(document.createTextNode('松散正文污染'))",
            "e => e.querySelector('[data-contents]').insertAdjacentHTML('beforeend',"
            "'<blockquote>未支持的正文块</blockquote>')",
        )
        for script in mutations:
            with self.subTest(mutation=script):
                await self.reset()
                await self.editor.evaluate(script)
                await self.assert_document_rejected()

    async def test_icon_decoration_exception_does_not_hide_extra_link_text(self):
        await self.editor.locator('a').first.locator('span').first.evaluate(
            "e => e.append(document.createTextNode('额外标签污染'))")
        await self.assert_document_rejected()

    async def test_icon_decoration_exception_does_not_hide_unknown_media(self):
        await self.editor.locator('a').first.locator('span').first.evaluate('''e => {
            const extra=document.createElement('img');
            extra.alt='未经原稿确认的图片';e.append(extra);
        }''')
        await self.assert_document_rejected()

    async def test_heading_level_or_later_equal_length_text_change_is_rejected(self):
        await self.editor.locator('h2').evaluate('''e => {
            const wrong=document.createElement('h3');
            for(const attr of e.attributes)wrong.setAttribute(attr.name,attr.value);
            wrong.innerHTML=e.innerHTML;e.replaceWith(wrong);
        }''')
        await self.assert_document_rejected()
        await self.reset()
        await verify_markdown_document(self.editor, EXPECTED)
        await self.editor.locator('div.Editable-unstyled [data-text=true]').first.evaluate(
            "e => e.textContent=e.textContent.replace('接口','接入')")
        await self.assert_document_rejected()


if __name__ == '__main__':
    unittest.main()
