"""Normal persistent browser onboarding and declarative editor filling.

No publish selector, arbitrary JavaScript, CAPTCHA solving, identity rotation or
blind retry. The site may autosave entered text; a filled editor is not a post.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit


WARNINGS = ('操作过于频繁', '账号异常', '帐号异常', '请完成安全验证', '请完成验证',
            '访问受限', '账号已被封禁', '发布权限受限', 'verify you are human', 'too many requests')


_BINDINGS = (
    ('微信', r'(?:微信\s*绑定|绑定\s*微信)'),
    ('手机', r'(?:(?:手机|手机号)\s*绑定|绑定\s*(?:手机|手机号))'),
    ('邮箱', r'(?:邮箱\s*绑定|绑定\s*邮箱)'),
    ('账号', r'(?:(?:账号|帐号)\s*绑定|绑定\s*(?:账号|帐号))'),
)
_BINDING_ACTION = r'(?:前往|立即|马上|去)\s*(?:绑定|完成绑定)'
_BINDING_NEGATION = r'(?:无需|无须|不需要|不必|不用|不强制|不要求|可以不|可不)[^。！？!?，,；;]{0,18}'
_CHALLENGES = (
    r'^(?:安全验证|人机验证)(?:$|[\s，。:：])',
    r'(?:请先|请|需要|必须|需先|须先)[^。！？!?]{0,20}(?:安全验证|人机验证)',
)


def _gate_patterns():
    return [{'name': name, 'negation': _BINDING_NEGATION + binding, 'patterns': [
        r'(?:发文|发布(?:文章|内容)?|投稿)(?:之前|前)[^。！？!?]{0,100}' + binding,
        binding + r'[^。！？!?]{0,50}(?:后才能|后才可|后方可|后再|才能|才可)(?:发文|发布|投稿)',
    ]} for name, binding in _BINDINGS]


def _text_gate(text):
    text = re.sub(r'\s+', ' ', text).strip().lower()
    if any(marker in text for marker in WARNINGS) or any(re.search(pattern, text) for pattern in _CHALLENGES):
        return 'warning'
    if re.search(_BINDING_ACTION, text):
        for binding in _gate_patterns():
            for pattern in binding['patterns']:
                match = re.search(pattern, text)
                if match and not re.search(binding['negation'], match.group()):
                    return binding['name']
    return None


_FRAME_GATE_JS = r'''options => {
    if (!document.body) return {readable: false};
    const text = document.body.innerText.replace(/\s+/g, ' ').trim().toLowerCase();
    if (options.warnings.some(marker => text.includes(marker)) ||
        options.challenges.some(pattern => new RegExp(pattern).test(text)))
        return {readable: true, gate: 'warning'};
    if (new RegExp(options.action).test(text)) {
        for (const binding of options.bindings) {
            for (const pattern of binding.patterns) {
                const match = text.match(new RegExp(pattern));
                if (match && !new RegExp(binding.negation).test(match[0]))
                    return {readable: true, gate: binding.name};
            }
        }
    }
    return {readable: true, gate: null};
}'''


async def _frame_visible(frame):
    # A child document can report visible content inside a hidden parent iframe.
    # Check every embedding element up to the main document, including opacity.
    while frame.parent_frame is not None:
        element = await frame.frame_element()
        try:
            if not await element.is_visible():
                return False
            if not await element.evaluate('''element => {
                for (let node=element; node; node=node.parentElement) {
                    const style=getComputedStyle(node);
                    if (Number(style.opacity) === 0 || style.contentVisibility === 'hidden') return false;
                }
                return true;
            }'''):
                return False
        finally:
            await element.dispose()
        frame = frame.parent_frame
    return True


def _raise_gate(gate):
    if gate == 'warning':
        raise ValueError('页面或可见嵌入页面出现验证、限制或账号异常提示，已暂停填写，请人工检查')
    if gate in {name for name, _ in _BINDINGS}:
        raise ValueError(f'平台要求发文前完成{gate}绑定，已暂停填写；请在专用窗口正常完成绑定后再检查')


async def _check_editor_gates(page):
    _raise_gate(_text_gate(await page.locator('body').inner_text(timeout=8000)))
    options = {'warnings': list(WARNINGS), 'challenges': list(_CHALLENGES),
               'action': _BINDING_ACTION, 'bindings': _gate_patterns()}
    for frame in list(page.frames):
        if frame is page.main_frame:
            continue
        try:
            visible = await asyncio.wait_for(_frame_visible(frame), timeout=3)
        except Exception:
            raise ValueError('无法确认嵌入页面是否可见，已暂停填写；请检查页面加载或安全验证后再试') from None
        if not visible:
            continue
        try:
            # Classify inside the frame: private text, URLs and account details
            # never enter the result, exception, progress event or saved record.
            result = await asyncio.wait_for(frame.evaluate(_FRAME_GATE_JS, options), timeout=3)
        except Exception:
            raise ValueError('无法读取可见嵌入页面的验证或账号要求，已暂停填写；请检查页面后再试') from None
        if (not isinstance(result, dict) or result.get('readable') is not True
                or result.get('gate') not in {None, 'warning', *[name for name, _ in _BINDINGS]}):
            raise ValueError('可见嵌入页面的验证或账号要求尚未确认，已暂停填写；请检查页面后再试')
        _raise_gate(result['gate'])


async def fill_editor(page, config, article):
    """Only fill a unique, empty, editable title/body on the configured origin."""
    expected, actual = urlsplit(config['editor_url']), urlsplit(page.url)
    if (actual.scheme, actual.hostname, actual.port) != (expected.scheme, expected.hostname, expected.port):
        raise ValueError('页面跳到了其他登录或验证域名，请先完成登录再重新检查')
    await _check_editor_gates(page)
    fields = []
    for name in ('title_selector', 'body_selector'):
        locator = page.locator('css=' + config[name])
        if await locator.count() != 1 or not await locator.is_visible() or not await locator.is_editable():
            raise ValueError('配置的编辑框不唯一、不可见或不可编辑；请更新平台配置')
        allowed = await locator.evaluate("el => (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text') || el.isContentEditable) && !el.querySelector('img,video,iframe,object,embed')")
        if not allowed:
            raise ValueError('只允许普通文本编辑框；不会填写密码、上传控件或覆盖已有媒体')
        # This fixed read-only expression is owned by the application; recipes
        # cannot supply code. locator.fill also supports contenteditable fields.
        existing = await locator.evaluate("el => el.isContentEditable ? el.innerText : el.value")
        if str(existing or '').strip():
            raise ValueError('编辑框已有内容，可能存在自动保存草稿；已暂停以免覆盖或重复')
        fields.append(locator)
    if await fields[0].evaluate('(el, selector) => { const other=document.querySelector(selector); return el === other || el.contains(other) || other.contains(el) }', config['body_selector']):
        raise ValueError('标题与正文编辑框重复或相互包含')
    # Recheck after field inspection so a gate loaded in the meantime prevents
    # the very first write, rather than being discovered after a draft is saved.
    await _check_editor_gates(page)
    await fields[0].fill(article['title'], timeout=8000)
    await fields[1].fill(article['content'], timeout=8000)
    title = await fields[0].evaluate('el => el.isContentEditable ? el.innerText : el.value')
    body = await fields[1].evaluate('el => el.isContentEditable ? el.innerText : el.value')
    if title != article['title'] or str(body).replace('\r\n', '\n') != article['content'].replace('\r\n', '\n'):
        raise ValueError('编辑器内容回读不一致，请人工检查；不会自动再次填写')
    return {'state': 'filled', 'message': '标题与正文已填入并回读核对；请在浏览器检查格式和提交，软件未确认已发布'}


async def open_session(target, profile_dir, executable_path, *, fallback_executable_path=None, article=None, progress=None, wait_seconds=1200):
    from playwright.async_api import async_playwright
    from services.geo_test_runner import exclusive_lock
    from services.browser_runtime import launch_context

    config = target['config']
    profile_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(profile_dir / '.geo-platform.lock'):
        async with async_playwright() as playwright:
            context = await launch_context(playwright, profile_dir,
                dict(executable_path=executable_path, headless=False, accept_downloads=False, viewport=None), fallback_executable_path)
            closed = asyncio.Event()
            context.on('close', lambda *_: closed.set())
            result = {'state': 'session_saved', 'message': '专用窗口会话已保存；登录和投稿权限尚未由适配器验证'}
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                response = await page.goto(config['editor_url'] if article else config['login_url'], wait_until='domcontentloaded', timeout=45000)
                if response and response.status >= 400:
                    raise ValueError('平台页面返回错误，已暂停；请核对入口与账号状态')
                if article:
                    try:
                        result = await fill_editor(page, config, article)
                    except Exception as error:
                        result = {'state': 'need_action', 'message': str(error) if isinstance(error, ValueError) else '编辑器加载或填写失败，请检查页面和配置；不会自动重试'}
                    if progress:
                        progress(result)
                elif progress:
                    progress({'state': 'login_open', 'message': '请在专用窗口正常注册、登录或验证；完成后关闭窗口以保存会话'})
                try:
                    await asyncio.wait_for(closed.wait(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    pass
                return result
            finally:
                try:
                    await context.close()
                except Exception as error:
                    if 'Target page, context or browser has been closed' not in str(error):
                        raise
