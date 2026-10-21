"""Bounded CSDN Markdown assistance through its normal visible editor.

This recipe imports reviewed text through normal controls. The platform may
autosave drafts; this recipe never clicks Save or Publish. Unknown drafts,
changed tutorials and additional dialogs require inspection instead of replay.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from urllib.parse import parse_qsl, urlsplit


TITLE = 'input.article-bar__title.article-bar__title--input.text-input'
TITLE_DISPLAY = '.article-bar__title-display'
BODY = 'pre.editor__inner.markdown-highlighting[contenteditable="true"]'
MEDIA = 'img,video,audio,svg,canvas,iframe,object,embed'
MORE = '.navigation_bar_more_actions_trigger'
MORE_POPUP = '.more-actions-popup'
IMPORT_INPUT = 'input#import-markdown-file-input'
READBACK_TIMEOUT_MS = 8000
# Complete rendered first-use tutorial observed on 2026-10-21 and compared to
# the official editor bundle. A partial title/substring is never sufficient.
WELCOME_SHA256 = '135e1417b75b60c777b4996d05326e1c91518cafed4bd5e067b3788836ade5f4'
WELCOME_MEDIA_SHA256 = '43f3df812ed00e69b79ec06ba5d2fed3bcb6976044427d41ce823e2c22990718'
EMPTY_TITLES = {'', '【无标题】'}


def supports(config):
    try:
        url = urlsplit(config.get('editor_url', ''))
        return (url.scheme == 'https' and url.hostname == 'editor.csdn.net'
                and url.port in (None, 443) and not url.username and not url.password
                and url.path in ('/md', '/md/') and not url.query and not url.fragment)
    except (TypeError, ValueError, AttributeError):
        return False


def _current_page_supported(value):
    try:
        url=urlsplit(value)
        if not supports({'editor_url':url._replace(query='').geturl()}):
            return False
        # The observed normal editor adds its autosaved draft ID to the URL.
        # Configured entry URLs remain query-free; this is not an article picker.
        query=parse_qsl(url.query,keep_blank_values=True)
        return not query or (len(query)==1 and query[0][0]=='articleId'
                             and query[0][1].isascii() and query[0][1].isdigit()
                             and 0<len(query[0][1])<=30 and int(query[0][1])>0)
    except (TypeError,ValueError):
        return False


async def _fields(page):
    if not _current_page_supported(page.url):
        raise ValueError('CSDN 编辑器已跳转，请先在专用窗口完成登录或验证')
    title, body = page.locator(TITLE), page.locator(BODY)
    if await title.count() != 1 or await body.count() != 1 or not await body.is_visible() or not await body.is_editable():
        raise ValueError('CSDN 编辑器结构尚未确认，请等待页面加载；不会填写或提交')
    if await title.get_attribute('type') not in (None, 'text'):
        raise ValueError('CSDN 标题输入类型尚未确认，已暂停；不会填写')
    title_value = await title.input_value(timeout=4000)
    text = await body.text_content(timeout=4000) or ''
    if not text.strip():
        state = 'empty'
    elif hashlib.sha256(text.encode('utf-8')).hexdigest() == WELCOME_SHA256:
        state = 'welcome_example'
    else:
        state = 'nonempty'
    media=await body.locator(MEDIA).evaluate_all('els=>els.map(e=>[e.tagName,e.getAttribute("src"),e.getAttribute("width"),e.getAttribute("height")])')
    if ((state == 'welcome_example' and hashlib.sha256(json.dumps(media,separators=(',',':')).encode()).hexdigest() != WELCOME_MEDIA_SHA256)
            or (media and state != 'welcome_example')):
        raise ValueError('CSDN 编辑器包含未确认的媒体内容，已保留；不会覆盖')
    return title, body, title_value, text, state


async def can_refresh_check_page(page):
    """Only the owned check tab's untouched blank/example may be reloaded."""
    try:
        _,_,title,_,body_state=await _fields(page)
        return title in EMPTY_TITLES and body_state in ('empty','welcome_example')
    except Exception:
        return False


async def probe_editor(page):
    from services.geo_destination_browser import _check_editor_gates
    await _check_editor_gates(page)
    _, _, title, _, body_state = await _fields(page)
    quota = page.locator('.publish-quota-tip')
    quota_empty = False
    if await quota.count() == 1 and await quota.is_visible():
        quota_empty = await quota.evaluate("e => /今日发文额度还有\\s*0\\s*篇/.test(e.innerText)")
    ready = title in EMPTY_TITLES and body_state in ('empty', 'welcome_example') and not quota_empty
    message = ('CSDN 今日发文额度已用完；稿件保留，请按平台额度安排发布' if quota_empty else
               'CSDN 已有标题或草稿内容，已保留；请先核对已有草稿，不会覆盖' if not ready else
               'CSDN 编辑器可用；已识别完整首次欢迎示例，可将已审稿文章正常导入为新文档' if body_state == 'welcome_example' else
               'CSDN 空白编辑器可用，可将已审稿文章正常导入为新文档')
    return {'phase': 'editor_ready' if ready else 'action_required', 'editor_ready': ready,
            'login_required': None, 'challenge': False, 'binding_required': False,
            'body_state': body_state, 'message': message}


async def _dismiss_template(page):
    # The real editor uses divs, not role=button. Only the observed template
    # dialog's Cancel/Add pair may be dismissed; account dialogs are untouched.
    cancel = page.locator('.modal .normal-cancel-btn').filter(has_text='取消')
    visible = [cancel.nth(i) for i in range(await cancel.count()) if await cancel.nth(i).is_visible()]
    if not visible:
        return
    if len(visible) != 1:
        raise ValueError('CSDN 出现多个待处理弹窗，请先核对；不会填写')
    modal = visible[0].locator('xpath=ancestor::div[contains(concat(" ",normalize-space(@class)," ")," modal ")][1]')
    add = modal.get_by_text('添加到正文', exact=True)
    if await add.count() != 1 or not await add.is_visible():
        raise ValueError('CSDN 弹窗类型尚未确认，请先核对；不会自动关闭')
    await visible[0].click(timeout=4000)


async def fill_editor(page, article, *, before_write):
    state = await probe_editor(page)
    if not state['editor_ready']:
        raise ValueError(state['message'])
    _, _, title_value, original_body, body_state = await _fields(page)
    if title_value not in EMPTY_TITLES or body_state not in ('empty','welcome_example'):
        raise ValueError('CSDN 在检查后加载了已有草稿，已保留；不会覆盖')
    return await _import_document(page, article, before_write=before_write,
        expected_source={'url': page.url, 'title': title_value, 'text': original_body})


async def _assert_source_unchanged(page, expected_source):
    _, _, title, body, _ = await _fields(page)
    if (page.url != expected_source['url'] or title != expected_source['title']
            or body != expected_source['text']):
        raise ValueError('CSDN 编辑内容在检查后发生变化，已保留；不会导入或重复填写')


async def _visible_unique(locator, message):
    visible = [locator.nth(i) for i in range(await locator.count()) if await locator.nth(i).is_visible()]
    if len(visible) != 1:
        raise ValueError(message)
    return visible[0]


def _import_text(article):
    if (not isinstance(article.get('title'), str) or not 5 <= len(article['title']) <= 100
            or not isinstance(article.get('content'), str) or not article['content'].strip()):
        raise ValueError('CSDN 标题应为 5 到 100 字且正文不能为空，请先审稿')
    content = article['content'].replace('\r\n', '\n').replace('\r', '\n')
    if content.startswith('\ufeff') or '<!--stackedit_data:' in content:
        raise ValueError('CSDN 导入只接受无 BOM、无编辑器恢复元信息的正文，请先审稿')
    if len(content.encode('utf-16-le')) // 2 > 250000:
        raise ValueError('CSDN 导入正文超过编辑器可完整读取的范围，请先编辑')
    # The observed importer preserves internal LF and adds a final LF only
    # when absent. Never ignore missing internal or additional trailing lines.
    return content, content if content.endswith('\n') else content + '\n'


async def _wait_imported_document(page, stem, expected_body):
    try:
        await page.wait_for_function(r'''args => {
            const titles=document.querySelectorAll(args.title);
            const editors=document.querySelectorAll(args.body);
            if (titles.length!==1 || editors.length!==1 || titles[0].value!==args.stem) return false;
            const editor=editors[0];
            const nodes=[...editor.childNodes].filter(n=>(n.textContent||'').trim());
            return nodes.length>0 && nodes.every(n=>n.nodeType===1 && n.classList.contains('cledit-section'))
                && editor.textContent.replace(/\r\n/g,'\n')===args.content;
        }''', arg={'title': TITLE, 'body': BODY, 'stem': stem, 'content': expected_body}, timeout=READBACK_TIMEOUT_MS)
    except Exception:
        raise ValueError('CSDN 尚未确认新文档标题与完整正文，已暂停；不会再次导入或提交') from None
    _, _, title, body, _ = await _fields(page)
    if title != stem or body.replace('\r\n', '\n') != expected_body:
        raise ValueError('CSDN 导入全文回读不一致，已暂停；不会再次导入或提交')


async def _import_document(page, article, *, before_write, expected_source):
    """One normal file import; explicit recovery must supply its exact checkpoint.

    Public fill_editor only supplies an empty/known-welcome source. A caller
    performing an explicitly authorized recovery must verify its partial draft
    against durable evidence before supplying expected_source here. The normal
    importer creates a new document; its unique file-derived title, not a stale
    articleId in the URL, is used to confirm that switch.
    """
    from services.geo_destination_browser import _check_editor_gates
    if (not isinstance(expected_source, dict) or set(expected_source) != {'url', 'title', 'text'}
            or not all(isinstance(value, str) for value in expected_source.values())):
        raise ValueError('CSDN 缺少精确原稿检查点，已暂停；不会导入')
    content, expected_body = _import_text(article)
    await _check_editor_gates(page)
    await _assert_source_unchanged(page, expected_source)
    await _dismiss_template(page)
    trigger = await _visible_unique(page.locator(MORE), 'CSDN 更多操作入口尚未确认，已暂停；不会导入')
    if (await trigger.inner_text()).strip() not in ('更多', '更多操作'):
        raise ValueError('CSDN 更多操作入口已变化，已暂停；不会导入')
    file_input = page.locator(IMPORT_INPUT)
    if (await file_input.count() != 1 or await file_input.get_attribute('type') != 'file'
            or await file_input.get_attribute('accept') != '.md'
            or await file_input.get_attribute('multiple') is not None or not await file_input.is_enabled()):
        raise ValueError('CSDN Markdown 导入控件尚未确认，已暂停；不会导入')
    popups = page.locator(MORE_POPUP)
    visible_popups = [popups.nth(i) for i in range(await popups.count()) if await popups.nth(i).is_visible()]
    if not visible_popups:
        await trigger.click(timeout=4000)
    popup = await _visible_unique(popups, 'CSDN 更多操作菜单尚未确认，已暂停；不会导入')
    importer = await _visible_unique(popup.get_by_role('button', name='导入', exact=True),
        'CSDN 导入按钮尚未确认，已暂停；不会导入')
    await _check_editor_gates(page)
    await _assert_source_unchanged(page, expected_source)
    # Keep this durable marker before even opening the chooser. A failed or
    # uncertain import never enters an automatic second attempt.
    saved = before_write()
    if inspect.isawaitable(saved):
        await saved
    await _check_editor_gates(page)
    await _assert_source_unchanged(page, expected_source)
    stem = 'geo-import-' + uuid.uuid4().hex
    try:
        async with page.expect_file_chooser(timeout=8000) as choosing:
            await importer.click(timeout=4000)
        chooser = await choosing.value
        element = chooser.element
        if (chooser.is_multiple() or await element.get_attribute('id') != 'import-markdown-file-input'
                or await element.get_attribute('type') != 'file' or await element.get_attribute('accept') != '.md'):
            raise ValueError('CSDN 文件选择器不匹配，已暂停；不会导入')
        await _check_editor_gates(page)
        await _assert_source_unchanged(page, expected_source)
        await chooser.set_files({'name': stem + '.md', 'mimeType': 'text/markdown', 'buffer': content.encode('utf-8')}, timeout=8000)
    except ValueError:
        raise
    except Exception:
        raise ValueError('CSDN 文件选择或读取未完成，已暂停；不会再次导入或提交') from None
    await _wait_imported_document(page, stem, expected_body)
    await _check_editor_gates(page)
    title, _, current_title, current_body, _ = await _fields(page)
    if current_title != stem or current_body.replace('\r\n', '\n') != expected_body:
        raise ValueError('CSDN 新文档在标题编辑前发生变化，已保留；不会再次导入')
    if not await title.is_visible():
        display = page.locator(TITLE_DISPLAY)
        if await display.count() != 1 or not await display.is_visible():
            raise ValueError('CSDN 标题入口尚未确认，请先核对；不会填写')
        await display.click(timeout=4000)
        await title.wait_for(state='visible', timeout=4000)
    if not await title.is_editable():
        raise ValueError('CSDN 标题当前不可编辑，请先核对')
    await _check_editor_gates(page)
    _, _, current_title, current_body, _ = await _fields(page)
    if current_title != stem or current_body.replace('\r\n', '\n') != expected_body:
        raise ValueError('CSDN 新文档在标题激活后发生变化，已保留；不会再次导入')
    await title.fill(article['title'], timeout=8000)
    await title.press('Enter')
    _, _, actual_title, actual_body, _ = await _fields(page)
    if actual_title != article['title'] or actual_body.replace('\r\n', '\n') != expected_body:
        raise ValueError('CSDN 编辑器全文回读不一致，已暂停；不会再次填写或提交')
    return {'state': 'filled', 'message': 'CSDN Markdown 正文已正常导入为新文档，标题与全文已逐字回读；尚未点击保存或发布，请继续核对声明与发布设置',
            'destination_session': {'phase': 'action_required', 'editor_ready': False,
                'content_write_attempted': True, 'can_check': True, 'body_state': 'reviewed_article',
                'message': '已填写，待核对发布设置；复检只读检查，不会再次填写'}}
