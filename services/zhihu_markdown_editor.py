"""Native Markdown import and read-only checks for the observed Zhihu writer."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from services.zhihu_publish_flow import ZhihuEditorNotReadyError


IMPORT_ACCEPT = '.docx,.markdown,.mdown,.mkdn,.md'
UPLOAD_LABEL = '点击选择本地文档或拖动文件到窗口上传'
_LINK = re.compile(r'\[([^\[\]\n]+)\]\((https?://[^\s()]+)\)')


def _error(code, message):
    return ZhihuEditorNotReadyError(message, reason_code=code)


def _normalize(text):
    return (text or '').replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')


def _inline(source):
    if '![' in source:
        raise _error('markdown_unsupported', 'Markdown 图片尚未支持自动核对，请先人工检查；本次未点击发布')
    links = []
    pieces = []
    offset = 0
    for match in _LINK.finditer(source):
        pieces.append(source[offset:match.start()])
        pieces.append(match.group(1))
        target = urlsplit(match.group(2))
        if not target.hostname or target.username or target.password:
            raise _error('markdown_unsupported', 'Markdown 链接格式需要人工核对；本次未点击发布')
        links.append({'text': match.group(1), 'href': match.group(2)})
        offset = match.end()
    pieces.append(source[offset:])
    text = ''.join(pieces)
    # This first native-import path supports the structures observed in the
    # real editor. Do not silently flatten unverified Markdown constructs.
    if re.search(r'[\[\]<>`*_\\]|!\[', text):
        raise _error('markdown_unsupported', 'Markdown 包含尚未支持核对的格式，请先调整或人工检查；本次未点击发布')
    return text, links


def build_markdown_manifest(markdown: str) -> list[dict]:
    """Describe supported Markdown without removing meaningful text or order."""
    lines = _normalize(markdown).strip().split('\n')
    result = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line != line.strip():
            raise _error('markdown_unsupported', 'Markdown 缩进或行尾空格需要人工核对；本次未点击发布')
        if line.startswith('|'):
            table_lines = []
            while index < len(lines) and lines[index].startswith('|'):
                table_lines.append(lines[index])
                index += 1
            cells = []
            for table_line in table_lines:
                if not table_line.endswith('|'):
                    raise _error('markdown_unsupported', 'Markdown 表格边界需要人工核对；本次未点击发布')
                cells.append([cell.strip() for cell in table_line[1:-1].split('|')])
            if (len(cells) < 3 or not cells[0] or
                    any(len(row) != len(cells[0]) for row in cells) or
                    any(not re.fullmatch(r':?-{3,}:?', cell) for cell in cells[1])):
                raise _error('markdown_unsupported', 'Markdown 表格格式需要人工核对；本次未点击发布')
            rows = [cells[0], *cells[2:]]
            for row in rows:
                for cell in row:
                    text, links = _inline(cell)
                    if links or not text:
                        raise _error('markdown_unsupported', 'Markdown 表格单元格格式需要人工核对；本次未点击发布')
            result.append({'type': 'table', 'rows': rows})
            continue
        heading = re.fullmatch(r'(#{1,2}) (.+)', line)
        if heading:
            text, links = _inline(heading.group(2))
            result.append({'type': 'heading', 'level': len(heading.group(1)), 'text': text, 'links': links})
        else:
            if re.match(r'^(?:#|>|[-+*] |\d+[.)] |---+$|===+$)', line):
                raise _error('markdown_unsupported', 'Markdown 包含尚未支持核对的结构，请先人工检查；本次未点击发布')
            text, links = _inline(line)
            result.append({'type': 'paragraph', 'text': text, 'links': links})
        index += 1
        if index < len(lines) and lines[index].strip() and not lines[index].startswith(('# ', '## ', '|')):
            raise _error('markdown_unsupported', 'Markdown 多行段落需要人工核对；本次未点击发布')
    if not result:
        raise _error('markdown_unsupported', 'Markdown 正文为空；本次未点击发布')
    return result


_READ_DOCUMENT = r'''editor => {
    const bad = detail => { throw new Error('unconfirmed_markdown_structure:' + detail); };
    const norm = value => (value || '').replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ');
    const shown = el => {
        for (let node=el; node && node.nodeType===1; node=node.parentElement) {
            const style=getComputedStyle(node);
            if (style.display==='none' || style.visibility==='hidden' || style.visibility==='collapse'
                || style.opacity==='0' || node.hidden || node.getAttribute('aria-hidden')==='true') return false;
        }
        return el.getClientRects().length > 0;
    };
    const decoration = el => el.tagName==='SPAN' && el.parentElement?.tagName==='A'
        && el.parentElement.getAttribute('data-editable')==='true'
        && el.children.length===1 && el.firstElementChild.matches('svg.ZDI--Link24')
        && [...el.firstElementChild.querySelectorAll('*')].every(child => child.tagName==='path')
        && el.firstElementChild.textContent==='' && el.textContent==='\u200b';
    const text = node => {
        if (node.nodeType===3) {
            if (node.textContent && !shown(node.parentElement)) bad('hidden_text');
            return node.textContent || '';
        }
        if (node.nodeType!==1) return '';
        if (decoration(node)) return '';
        if (['SCRIPT','STYLE','SVG','IMG','VIDEO','IFRAME'].includes(node.tagName)) bad('unknown_element');
        if (node.tagName==='BR') return '\n';
        return [...node.childNodes].map(text).join('');
    };
    if (!shown(editor) || !editor.matches('.public-DraftEditor-content[contenteditable="true"]')) bad('editor');
    if (editor.querySelector('img,video,iframe,script,style,object,embed')) bad('unknown_media');
    const containers=[...editor.children].filter(el => el.getAttribute('data-contents')==='true');
    if (containers.length!==1 || editor.children.length!==1) bad('contents');
    if ([...editor.childNodes].some(node => node.nodeType===3 && node.textContent.trim())) bad('outside_contents');
    const contents=containers[0], result=[];
    for (const block of contents.childNodes) {
        if (block.nodeType===3 && !block.textContent.trim()) continue;
        if (block.nodeType!==1 || block.getAttribute('data-block')!=='true' || !shown(block)) bad('block');
        if (block.querySelector('[data-block="true"]')) bad('nested_block');
        if (block.tagName==='FIGURE') {
            const tables=block.querySelectorAll('table[data-draft-type="table"]');
            if (tables.length!==1 || block.querySelectorAll('table').length!==1) bad('table');
            const table=tables[0];
            const rows=[...table.rows].map((row,index) => [...row.cells].map(cell => {
                if (cell.tagName!==(index===0?'TH':'TD') || cell.colSpan!==1 || cell.rowSpan!==1) bad('cell');
                const inputs=cell.querySelectorAll('.Table-dataInput[contenteditable="true"]');
                if (inputs.length!==1 || !shown(inputs[0]) || cell.querySelector('a')) bad('cell_input');
                const value=norm(text(inputs[0]));
                if (norm(text(cell))!==value) bad('cell_extra');
                return value;
            }));
            const outside = node => {
                if (node===table) return '';
                if (node.nodeType===3) return node.textContent || '';
                return [...node.childNodes].map(outside).join('');
            };
            if (outside(block).trim()) bad('table_extra');
            result.push({type:'table',rows});
            continue;
        }
        const value=norm(text(block));
        if (block.tagName==='DIV' && block.classList.contains('Editable-unstyled') && !value.trim()) continue;
        const links=[...block.querySelectorAll('a')].map(link => {
            if (link.getAttribute('data-editable')!=='true' || !shown(link)) bad('link');
            return {text:norm(text(link)),href:link.getAttribute('href') || ''};
        });
        if (block.tagName==='DIV' && block.classList.contains('Editable-unstyled')) {
            result.push({type:'paragraph',text:value,links});
        } else if (block.tagName==='H2' || block.tagName==='H3') {
            result.push({type:'heading',level:Number(block.tagName.slice(1))-1,text:value,links});
        } else bad('block_type');
    }
    return result;
}'''


async def read_markdown_document(editor) -> list[dict]:
    try:
        return await editor.evaluate(_READ_DOCUMENT)
    except Exception as exc:
        raise _error('markdown_semantic_mismatch', '知乎导入后的正文结构尚未通过核对；本次未点击发布') from exc


async def verify_markdown_document(editor, manifest) -> None:
    if await read_markdown_document(editor) != manifest:
        raise _error('markdown_semantic_mismatch', '知乎导入后的段落、链接或表格与原稿不一致；本次未点击发布')


def _writer_url(url):
    parsed = urlsplit(str(url or ''))
    return (parsed.scheme == 'https' and parsed.netloc == 'zhuanlan.zhihu.com'
            and not parsed.fragment and not parsed.query
            and (parsed.path == '/write' or re.fullmatch(r'/p/\d+/edit', parsed.path)))


async def _unique_visible(locator):
    matches = [locator.nth(i) for i in range(await locator.count()) if await locator.nth(i).is_visible()]
    if len(matches) != 1 or not await matches[0].is_enabled():
        raise _error('markdown_import_controls_unconfirmed', '知乎文档导入入口尚未确认，请先人工核对；本次未点击发布')
    return matches[0]


async def import_markdown_body(page, editor, *, markdown, expected_title, manifest,
                               wait_seconds=20.0) -> None:
    """One ordinary native file import into the caller's confirmed empty writer."""
    async def assert_source():
        title = await _unique_visible(page.locator('textarea[placeholder*="标题"], input[placeholder*="标题"]'))
        if not _writer_url(page.url) or _normalize(await title.input_value()) != expected_title:
            raise _error('markdown_import_unconfirmed', '知乎标题或编辑页面发生变化，已停止导入；本次未点击发布')
        if (await editor.inner_text()).strip():
            raise _error('editor_existing_content', '知乎正文已有内容，已停止以免覆盖；本次未点击发布')

    await assert_source()
    try:
        trigger = await _unique_visible(page.get_by_role('button', name='导入', exact=True))
        await trigger.click(timeout=5000)
        document = page.get_by_role('button', name='导入文档', exact=True)
        await document.first.wait_for(state='visible', timeout=5000)
        await (await _unique_visible(document)).click(timeout=5000)
        upload = page.get_by_role('button', name=UPLOAD_LABEL, exact=True)
        await upload.first.wait_for(state='visible', timeout=5000)
        upload_button = await _unique_visible(upload)
        file_input = page.locator(f'input[type="file"][accept="{IMPORT_ACCEPT}"]')
        if (await file_input.count() != 1 or not await file_input.is_enabled()
                or await file_input.get_attribute('multiple') is not None):
            raise _error('markdown_import_controls_unconfirmed', '知乎文档导入文件控件尚未确认；本次未点击发布')
        await assert_source()
        async with page.expect_file_chooser(timeout=8000) as pending:
            await upload_button.click(timeout=5000)
        chooser = await pending.value
        if (chooser.is_multiple() or await chooser.element.get_attribute('type') != 'file'
                or await chooser.element.get_attribute('accept') != IMPORT_ACCEPT):
            raise _error('markdown_import_controls_unconfirmed', '知乎文档选择器与预期不一致；本次未点击发布')
        await assert_source()
        await chooser.set_files({'name': 'geo-article-body.md', 'mimeType': 'text/markdown',
                                 'buffer': markdown.encode('utf-8')}, timeout=10000)
    except ZhihuEditorNotReadyError:
        raise
    except Exception as exc:
        raise _error('markdown_import_unconfirmed', '知乎文档导入尚未确认，已保留原稿，请先人工核对；本次未点击发布') from exc
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        if not _writer_url(page.url):
            raise _error('markdown_import_unconfirmed', '知乎导入后页面发生异常变化；本次未点击发布')
        try:
            await verify_markdown_document(editor, manifest)
            return
        except ZhihuEditorNotReadyError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
        await asyncio.sleep(.2)
