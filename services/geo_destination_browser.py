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
    const challenge = text.includes('请完成验证') || text.includes('verify you are human') ||
        options.challenges.some(pattern => new RegExp(pattern).test(text));
    if (options.warnings.some(marker => text.includes(marker)) || challenge)
        return {readable: true, gate: 'warning', challenge};
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


class _DestinationGateError(ValueError):
    def __init__(self, message, state, binding_kind=None):
        super().__init__(message)
        self.state = state
        self.binding_kind = binding_kind


def _challenge_text(text):
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return (any(marker in text for marker in ('请完成验证', 'verify you are human'))
            or any(re.search(pattern, text) for pattern in _CHALLENGES))


def _raise_gate(gate, challenge=None):
    if gate == 'warning':
        raise _DestinationGateError('页面或可见嵌入页面出现验证、限制或账号异常提示，已暂停填写，请人工检查',
                                    'challenge' if challenge is True else 'action_required')
    if gate in {name for name, _ in _BINDINGS}:
        kind = {'微信': 'wechat', '手机': 'phone', '邮箱': 'email', '账号': 'account'}[gate]
        raise _DestinationGateError(f'平台要求发文前完成{gate}绑定，已暂停填写；请在专用窗口正常完成绑定后再检查',
                                    'binding_required', kind)


async def _check_editor_gates(page):
    text = await page.locator('body').inner_text(timeout=8000)
    _raise_gate(_text_gate(text), _challenge_text(text))
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
        _raise_gate(result['gate'], result.get('challenge'))


class _EditorReadinessError(ValueError):
    """A fixed application message, safe to show without a DOM excerpt."""


async def _editor_fields(page, config):
    """Read-only proof of a configured, empty, ordinary text editor."""
    if not config.get('title_selector') or not config.get('body_selector'):
        raise _EditorReadinessError('尚未配置可核验的标题和正文编辑框；请先完善平台适配，当前可以登录或导出稿件')
    expected, actual = urlsplit(config['editor_url']), urlsplit(page.url)
    if (actual.scheme, actual.hostname, actual.port) != (expected.scheme, expected.hostname, expected.port):
        raise _EditorReadinessError('页面跳到了其他登录或验证域名，请先完成登录再重新检查')
    await _check_editor_gates(page)
    fields = []
    for name in ('title_selector', 'body_selector'):
        locator = page.locator('css=' + config[name])
        if await locator.count() != 1 or not await locator.is_visible() or not await locator.is_editable():
            raise _EditorReadinessError('配置的编辑框不唯一、不可见或不可编辑；请更新平台配置')
        allowed = await locator.evaluate("el => (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text') || el.isContentEditable) && !el.querySelector('img,video,iframe,object,embed')")
        if not allowed:
            raise _EditorReadinessError('只允许普通文本编辑框；不会填写密码、上传控件或覆盖已有媒体')
        # This fixed read-only expression is owned by the application; recipes
        # cannot supply code. locator.fill also supports contenteditable fields.
        existing = await locator.evaluate("el => el.isContentEditable ? el.innerText : el.value")
        if str(existing or '').strip():
            raise _EditorReadinessError('编辑框已有内容，可能存在自动保存草稿；已暂停以免覆盖或重复')
        fields.append(locator)
    if await fields[0].evaluate('(el, selector) => { const other=document.querySelector(selector); return el === other || el.contains(other) || other.contains(el) }', config['body_selector']):
        raise _EditorReadinessError('标题与正文编辑框重复或相互包含')
    return fields


async def fill_editor(page, config, article, *, before_write=None):
    """Only fill once; durable reservation and write marker precede any write."""
    fields = await _editor_fields(page, config)
    # Recheck after field inspection so a gate loaded in the meantime prevents
    # the very first write, rather than being discovered after a draft is saved.
    await _check_editor_gates(page)
    if before_write:
        # Synchronous persistence must succeed before the first potentially
        # autosaved keystroke. An exception prevents every editor write.
        before_write()
    await fields[0].fill(article['title'], timeout=8000)
    await fields[1].fill(article['content'], timeout=8000)
    title = await fields[0].evaluate('el => el.isContentEditable ? el.innerText : el.value')
    body = await fields[1].evaluate('el => el.isContentEditable ? el.innerText : el.value')
    if title != article['title'] or str(body).replace('\r\n', '\n') != article['content'].replace('\r\n', '\n'):
        raise ValueError('编辑器内容回读不一致，请人工检查；不会自动再次填写')
    return {'state': 'filled', 'message': '标题与正文已填入并回读核对；请在浏览器检查格式和提交，软件未确认已发布'}


_SESSION_MESSAGES = {
    'opening': '正在打开专用平台窗口；请在当前窗口正常完成登录、验证或绑定。',
    'checking': '正在检查当前平台会话和编辑器，不会提交文章或重复填写。',
    'login_required': '平台仍要求登录；请在专用窗口正常完成，再点击“检查并继续”。',
    'challenge': '平台仍显示安全验证；请在专用窗口正常完成，再点击“检查并继续”。',
    'binding_required': '平台要求完成账号绑定；请在专用窗口正常完成，再点击“检查并继续”。',
    'editor_ready': '已只读核验空白且可编辑的标题与正文；尚未填写或发布文章。',
    'action_required': '编辑器能力或当前页面状态尚未核验；请检查窗口和平台适配配置。',
    'window_closed': '专用窗口已结束；会话保存和登录状态未确认，不会自动重新填写。',
}


def _session_state(state, *, write_attempted=False, window_open=True, binding_kind=None, message=None):
    value = {'state': state, 'message': message or _SESSION_MESSAGES[state],
             'can_check': window_open and state not in ('opening', 'checking'),
             'window_open': window_open, 'content_write_attempted': write_attempted,
             'editor_ready': state == 'editor_ready' and not write_attempted,
             'login_required': True if state == 'login_required' else None,
             'challenge': True if state == 'challenge' else None,
             'binding_required': True if state == 'binding_required' else None}
    if binding_kind in ('wechat', 'phone', 'email', 'account'):
        value['binding_kind'] = binding_kind
    return value


_LOGIN_PROBE_JS = r'''() => {
    if (!document.body) return {readable:false};
    const visible = el => {
        if (!el || !el.getClientRects().length) return false;
        for (let node=el; node; node=node.parentElement) {
            const style=getComputedStyle(node);
            if (style.display==='none' || style.visibility==='hidden' || Number(style.opacity)===0) return false;
        }
        return true;
    };
    const text=document.body.innerText.replace(/\s+/g,' ').trim();
    const login = [...document.querySelectorAll('input[type="password"]')].some(visible) ||
        /(?:扫码登录|扫描二维码登录|请先登录|登录后(?:才可|才能|方可)?(?:发文|发布|继续))/.test(text) ||
        ([...document.querySelectorAll('input[type="tel"]')].some(visible) && /(?:验证码登录|登录\/注册|登录或注册)/.test(text));
    return {readable:true, login_required:login};
}'''


async def _observe_session(page, config, *, inspect_editor=False, write_attempted=False):
    """Return only fixed status fields, never a DOM excerpt or account value."""
    try:
        await _check_editor_gates(page)
        for frame in list(page.frames):
            if frame is not page.main_frame and not await asyncio.wait_for(_frame_visible(frame), 3):
                continue
            observed = await asyncio.wait_for(frame.evaluate(_LOGIN_PROBE_JS), 3)
            if not isinstance(observed, dict) or observed.get('readable') is not True:
                return _session_state('action_required', write_attempted=write_attempted)
            if observed.get('login_required') is True:
                return _session_state('login_required', write_attempted=write_attempted)
        if write_attempted:
            return _session_state('action_required', write_attempted=True,
                message='本任务已尝试写入内容；请检查当前编辑页，复检仅查看状态，不会重新填写或确认已发布。')
        if inspect_editor:
            from services import csdn_editor
            if csdn_editor.supports(config):
                observed = await csdn_editor.probe_editor(page)
                state = ('editor_ready' if observed.get('editor_ready') is True else
                         observed.get('phase') if observed.get('phase') in _SESSION_MESSAGES else 'action_required')
                value = _session_state(state)
                if observed.get('body_state') in ('empty', 'welcome_example', 'nonempty', 'unknown'):
                    value['body_state'] = observed['body_state']
                # Only this application-owned adapter supplies fixed messages.
                if isinstance(observed.get('message'), str):
                    value['message'] = observed['message']
                return value
            await _editor_fields(page, config)
            return _session_state('editor_ready')
        return _session_state('action_required', message='请在当前窗口完成登录；完成后点击“检查并继续”，软件会在同一会话核对编辑入口。')
    except _DestinationGateError as error:
        return _session_state(error.state, write_attempted=write_attempted, binding_kind=error.binding_kind, message=str(error))
    except _EditorReadinessError as error:
        return _session_state('action_required', write_attempted=write_attempted, message=str(error))
    except Exception:
        # A detached/inaccessible frame or a changing page is not authentication
        # evidence. In particular do not persist Playwright's private URL/text.
        return _session_state('action_required', write_attempted=write_attempted)


async def _check_tab_has_content(page, config):
    """Unknown or nonempty fields prevent refreshing a user-editable check tab."""
    selectors = [config.get('title_selector'), config.get('body_selector')]
    try:
        from services import csdn_editor
        if csdn_editor.supports(config):
            return not await csdn_editor.can_refresh_check_page(page)
        if all(selectors):
            for selector in selectors:
                field = page.locator('css=' + selector)
                if await field.count() != 1:
                    return True
                if await field.evaluate("el => Boolean(String(el.isContentEditable ? el.innerText : (el.value || '')).trim())"):
                    return True
            return False
        return await page.evaluate('''() => [...document.querySelectorAll('textarea,[contenteditable="true"],input[type="text"]')]
            .some(el => Boolean(String(el.isContentEditable ? el.innerText : (el.value || '')).trim()))''') is not False
    except Exception:
        return True


async def open_session(target, profile_dir, executable_path, *, fallback_executable_path=None, article=None,
                       progress=None, check_requested=None, wait_seconds=None):
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
            write_attempted, filled = False, False
            session = _session_state('opening')
            check_page = None
            result = None

            def emit(observation):
                nonlocal session, result
                session = observation
                state = 'filled' if filled else ('preparing' if article and observation['state'] in ('opening', 'checking')
                         else 'need_action' if article else 'login_open')
                result = {'state': state, 'message': observation['message'], 'destination_session': observation,
                          'session_saved': None}
                if progress:
                    progress(result)

            def before_write():
                nonlocal write_attempted
                if write_attempted:
                    raise ValueError('本任务已有写入尝试，不会重新填写')
                write_attempted = True
                emit(_session_state('checking', write_attempted=True,
                     message='已在写入前保留本次尝试；随后只填写一次，不会自动重复。'))

            async def inspect_and_maybe_fill(active_page, *, inspect_editor):
                nonlocal filled
                observation = await _observe_session(active_page, config, inspect_editor=inspect_editor,
                                                     write_attempted=write_attempted)
                if article and observation['state'] == 'editor_ready' and not write_attempted:
                    try:
                        from services import csdn_editor
                        if csdn_editor.supports(config):
                            await csdn_editor.fill_editor(active_page, article, before_write=before_write)
                        else:
                            await fill_editor(active_page, config, article, before_write=before_write)
                        filled = True
                        observation = _session_state('action_required', write_attempted=True,
                            message='标题与正文已填入并回读核对；请在浏览器检查格式和提交，软件未确认已发布。')
                    except _DestinationGateError as error:
                        observation = _session_state(error.state, write_attempted=write_attempted, binding_kind=error.binding_kind, message=str(error))
                    except Exception:
                        observation = _session_state('action_required', write_attempted=write_attempted,
                            message='编辑器加载或填写未完成；请检查当前页，软件不会自动再次填写。')
                emit(observation)

            try:
                emit(session)
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    response = await page.goto(config['editor_url'] if article else config['login_url'], wait_until='domcontentloaded', timeout=45000)
                except Exception:
                    raise ValueError('平台入口未能正常加载，请检查专用窗口；不会自动重新填写。') from None
                if response and response.status >= 400:
                    raise ValueError('平台页面返回错误，已暂停；请核对入口与账号状态')
                if not closed.is_set():
                    await inspect_and_maybe_fill(page, inspect_editor=article is not None)
                deadline = asyncio.get_running_loop().time() + wait_seconds if wait_seconds is not None else None
                reason = 'window_closed'
                while not closed.is_set():
                    remaining = deadline - asyncio.get_running_loop().time() if deadline is not None else None
                    if remaining is not None and remaining <= 0:
                        reason = 'wait_elapsed'
                        break
                    waiters = [asyncio.create_task(closed.wait())]
                    if check_requested is not None:
                        waiters.append(asyncio.create_task(check_requested.wait()))
                    try:
                        done, _ = await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for waiter in waiters:
                            if not waiter.done():
                                waiter.cancel()
                        await asyncio.gather(*waiters, return_exceptions=True)
                    if closed.is_set():
                        break
                    if not done:
                        reason = 'wait_elapsed'
                        break
                    if check_requested is None or not check_requested.is_set():
                        continue
                    check_requested.clear()
                    emit(_session_state('checking', write_attempted=write_attempted))
                    try:
                        if write_attempted:
                            # Once any write was attempted, every user check is
                            # read-only in that exact page. No new tab or fill.
                            await inspect_and_maybe_fill(check_page or page, inspect_editor=False)
                        else:
                            # Binding can complete in another tab while an old
                            # editor iframe remains cached. Preserve the original
                            # login/verification page and reuse one controlled tab.
                            inspect_only = False
                            if check_page is None or check_page.is_closed():
                                check_page = await context.new_page()
                            elif await _check_tab_has_content(check_page, config):
                                inspect_only = True
                            if not inspect_only:
                                response = await check_page.goto(config['editor_url'], wait_until='domcontentloaded', timeout=45000)
                                if response and response.status >= 400:
                                    emit(_session_state('action_required'))
                                    continue
                            await inspect_and_maybe_fill(check_page, inspect_editor=True)
                            if not article and session['state'] == 'editor_ready':
                                session = {**session, 'can_check': False, 'window_open': False}
                                return {'state': 'editor_ready', 'message': session['message'],
                                        'destination_session': session, 'session_saved': None,
                                        'close_reason': 'editor_checked'}
                    except Exception:
                        emit(_session_state('action_required', write_attempted=write_attempted))
                previous = session['state']
                emit(_session_state('window_closed', write_attempted=write_attempted, window_open=False))
                result['state'] = 'filled' if filled else 'need_action'
                result['destination_session']['last_state'] = previous
                result['close_reason'] = reason
                return result
            finally:
                try:
                    await context.close()
                except Exception as error:
                    if 'Target page, context or browser has been closed' not in str(error):
                        raise ValueError('平台窗口已结束，连接状态需检查；软件不会自动重新填写。') from None
