"""Bounded CSDN Markdown assistance through its normal visible editor.

This recipe fills reviewed text, never saves or submits a post. Unknown drafts,
changed tutorials and additional dialogs require inspection instead of replay.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from urllib.parse import parse_qsl, urlsplit


TITLE = 'input.article-bar__title.article-bar__title--input.text-input'
TITLE_DISPLAY = '.article-bar__title-display'
BODY = 'pre.editor__inner.markdown-highlighting[contenteditable="true"]'
MEDIA = 'img,video,audio,svg,canvas,iframe,object,embed'
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
    if media and (state!='welcome_example' or hashlib.sha256(json.dumps(media,separators=(',',':')).encode()).hexdigest()!=WELCOME_MEDIA_SHA256):
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
               'CSDN 编辑器可用；检测到完整首次欢迎示例，填写时将由已审稿文章替换示例' if body_state == 'welcome_example' else
               'CSDN 空白编辑器可用，可继续填写已审稿文章')
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
    from services.geo_destination_browser import _check_editor_gates
    state = await probe_editor(page)
    if not state['editor_ready']:
        raise ValueError(state['message'])
    if not isinstance(article.get('title'), str) or not 5 <= len(article['title']) <= 100 or not isinstance(article.get('content'), str) or not article['content'].strip():
        raise ValueError('CSDN 标题应为 5 到 100 字且正文不能为空，请先审稿')
    title, body, title_value, original_body, body_state = await _fields(page)
    if title_value not in EMPTY_TITLES or body_state not in ('empty','welcome_example'):
        raise ValueError('CSDN 在检查后加载了已有草稿，已保留；不会覆盖')
    original_url=page.url
    await _dismiss_template(page)
    if not await title.is_visible():
        display = page.locator(TITLE_DISPLAY)
        if await display.count() != 1 or not await display.is_visible():
            raise ValueError('CSDN 标题入口尚未确认，请先核对；不会填写')
        await display.click(timeout=4000)
        await title.wait_for(state='visible', timeout=4000)
    if not await title.is_editable():
        raise ValueError('CSDN 标题当前不可编辑，请先核对')
    await _check_editor_gates(page)
    _,_,current_title,current_body,current_state=await _fields(page)
    if (page.url!=original_url or current_title!=title_value or current_body!=original_body
            or current_state not in ('empty','welcome_example')):
        raise ValueError('CSDN 编辑内容在检查后发生变化，已暂停以免覆盖')
    # Persist the attempt before the first potentially autosaved content write.
    saved = before_write()
    if inspect.isawaitable(saved):
        await saved
    _,_,current_title,current_body,current_state=await _fields(page)
    if (page.url!=original_url or current_title!=title_value or current_body!=original_body
            or current_state not in ('empty','welcome_example')):
        raise ValueError('CSDN 在保留写入记录时发生变化，已暂停；不会重复填写')
    await title.fill(article['title'], timeout=8000)
    await title.press('Enter')
    _,_,current_title,current_body,current_state=await _fields(page)
    if current_title!=article['title'] or current_body!=original_body or current_state not in ('empty','welcome_example'):
        raise ValueError('CSDN 在标题输入后加载了其他内容，已保留正文；不会再次填写')
    await body.fill(article['content'], timeout=8000)
    # Wait for the editor's own tokenized document, rather than accepting the
    # browser's transient contenteditable DOM before the editor handles input.
    try:
        await page.wait_for_function('''args => {
            const editor=document.querySelector(args.selector);
            if (!editor) return false;
            const nodes=[...editor.childNodes].filter(n=>(n.textContent||'').trim());
            if (!nodes.length || !nodes.every(n=>n.nodeType===1 && n.classList.contains('cledit-section'))) return false;
            return editor.textContent.replace(/\\r\\n/g,'\\n').replace(/\\n+$/,'') === args.content.replace(/\\r\\n/g,'\\n').replace(/\\n+$/,'');
        }''', arg={'selector': BODY, 'content': article['content']}, timeout=4000)
    except Exception:
        raise ValueError('CSDN 尚未确认编辑器已接收正文，已暂停；不会再次填写或提交') from None
    actual_title = await title.input_value()
    actual_body = (await body.text_content() or '').replace('\r\n', '\n')
    expected_body = article['content'].replace('\r\n', '\n')
    if actual_title != article['title'] or actual_body.rstrip('\n') != expected_body.rstrip('\n'):
        raise ValueError('CSDN 编辑器全文回读不一致，已暂停；不会再次填写或提交')
    return {'state': 'filled', 'message': 'CSDN 标题与 Markdown 正文已填写并逐字回读；尚未点击保存或发布，请继续核对声明与发布设置',
            'destination_session': {'phase': 'action_required', 'editor_ready': False,
                'content_write_attempted': True, 'can_check': True, 'body_state': 'reviewed_article',
                'message': '已填写，待核对发布设置；复检只读检查，不会再次填写'}}
