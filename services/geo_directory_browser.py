"""Normal GrokCode form assistance with one exact, human-triggered lead POST.

No final-submit click, screenshots, page/body persistence or automatic retry.
The service owns durable intent/receipt transactions and frozen public fields.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from services.geo_directory_service import ENTRY_URL, LIST_URL, LEAD_URL, expected_transport


MESSAGES = {
    'opening': '正在打开目录专用窗口；只准备已审阅公开字段，最终提交由本人操作。',
    'awaiting_human_verification': '请本人在当前浏览器正常完成登录或验证；软件只等待已核对目录表单出现，不读取密码、验证码或认证内容。',
    'awaiting_human_confirmation': '七个公开字段已逐字回读一致。请本人在官方页面核对并决定是否提交；软件不会点击最终提交。不提交时，请从软件结束本次窗口。',
    'needs_manual_review': '页面控件或公开字段尚未核对一致，请在保留的窗口检查；软件不会重新填写或提交。',
    'submission_intent': '本次唯一提交意图已保存；正在等待同一请求的官方回执，请勿再次提交。',
    'submitted_pending_review': '已取得同一请求的官方持久接收回执，进入待核验；这不代表公开目录收录、搜索索引或GEO效果。',
    'submission_unknown': '提交状态无法完整确认，原记录已保留；请核对原窗口或平台记录，不会自动重发。',
    'closed_not_submitted': '软件已受控结束本次窗口，并确认未放行目录申请提交；可重新审阅同一冻结申请。',
    'inspection_open': '已打开固定目录列表，只读核对；不会填写、提交或改变原申请锁定状态。',
    'inspection_closed': '只读核对窗口已结束；原申请及提交状态保持。',
}


async def _callback(function, *args):
    if function is None:
        return None
    result = function(*args)
    return await result if inspect.isawaitable(result) else result


async def _owned_operation(operation, closed, close_requested, state):
    """A controlled-close signal interrupts only our current browser operation."""
    task = asyncio.create_task(operation)
    waiters = [task, asyncio.create_task(closed.wait())]
    if close_requested is not None:
        waiters.append(asyncio.create_task(close_requested.wait()))
    try:
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if close_requested is not None and close_requested.is_set() and not closed.is_set():
            state.closing = True
            return False, None
        if closed.is_set():
            return False, None
        if task in done:
            return True, await task
        return False, None
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


def _request_kind(request):
    """Classify by public URL metadata without touching authentication bodies.

    Only known/suspected directory-business writes are fenced. Ordinary login,
    CAPTCHA, telemetry and unrelated browser traffic keep their normal behavior.
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return 'ordinary'
    parsed = urlsplit(request.url)
    if parsed.hostname != 'grokcode.cn':
        return 'ordinary'
    parts = [part for part in parsed.path.casefold().split('/') if part]
    if parsed.hostname == 'grokcode.cn' and parsed.path.casefold().startswith('/api/lead'):
        return 'business'
    if any(re.search(r'auth|login|signin|sign-in|signup|sign-up|session|oauth|password|token|security|cookie|captcha|verify|verification|challenge|turnstile|telemetry|analytics|metrics|beacon|tracking', part) for part in parts):
        return 'ordinary'
    # A same-origin near-lead endpoint is still business, even with query/path
    # changes. Third-party browser requests above are never parsed for fields.
    if any(part.startswith('lead') for part in parts):
        return 'business'
    return 'business' if any(re.search(r'lead|submit|transit|intake', part) for part in parts) else 'unknown_same_origin'


def directory_write(request):
    return _request_kind(request) == 'business'


def submit_observer_script(binding, marker):
    options=json.dumps({'binding':binding,'marker':marker,'entry':ENTRY_URL,'list':LIST_URL,
                        'names':['name','url','rate','source','contact','notes']})
    return '''(() => {
      const options = ''' + options + ''';
      window[options.marker] = false;
      document.addEventListener('submit', event => {
        if (![options.entry, options.list].includes(location.href)) return;
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        const known = options.names.every(name => {
          const controls = form.querySelectorAll('input[name="' + name + '"]');
          return controls.length === 1 && ['text', 'url'].includes(controls[0].type);
        }) && form.querySelectorAll('input[type="checkbox"]').length > 0;
        if (!known) return;
        window[options.marker] = true;
        const notify = window[options.binding];
        if (typeof notify === 'function') Promise.resolve(notify()).catch(() => {});
      }, true);
    })();'''


class SubmissionGuard:
    """Session-local network fence; callbacks must persist before continuation."""
    def __init__(self, expected, *, before_submit, save_receipt, progress=None, inspection_only=False):
        if not inspection_only and (not callable(before_submit) or not callable(save_receipt)):
            raise ValueError('目录提交必须由受控服务保存意图与回执')
        self.expected = copy.deepcopy(expected)
        self.before_submit, self.save_receipt, self.progress = before_submit, save_receipt, progress
        self.inspection_only = inspection_only
        # These count directory-business requests only. Authentication and
        # ordinary browser traffic are neither counted nor read for bodies.
        self.allowed_posts = self.blocked_writes = 0
        self.intent_attempted = self.receipt_attempted = self.receipt_saved = False
        self.unsafe = self.form_ready = self.route_guard_installed = False
        self.form_scope_check = lambda: True
        self.form_submit_seen = False
        self.submit_signal_read = None
        self.closing = False
        self.request = None
        self.stage = 'inspection_open' if inspection_only else 'opening'
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.routes = set()

    async def emit(self, stage, *, window_open=True):
        self.stage = stage
        value = {'stage': stage, 'message': MESSAGES[stage], 'window_open': window_open,
                 'inspection_only': self.inspection_only, 'allowed_posts': self.allowed_posts,
                 'blocked_writes': self.blocked_writes}
        try:
            await _callback(self.progress, value)
        except Exception:
            self.unsafe = True
            raise

    async def _unknown(self):
        self.unsafe = True
        if not self.inspection_only and not self.receipt_saved:
            try:
                await self.emit('submission_unknown')
            except Exception:
                pass

    async def submit_observed(self):
        if not self.form_submit_seen and self.submit_signal_read is not None:
            self.form_submit_seen = await _callback(self.submit_signal_read) is True
        return self.form_submit_seen

    async def route(self, route):
        task = asyncio.current_task()
        self.routes.add(task)
        try:
            request = route.request
            kind = _request_kind(request)
            if kind == 'unknown_same_origin' and await self.submit_observed():
                kind = 'business'
            if kind != 'business':
                try:
                    await route.continue_()
                except Exception:
                    if not self.closing:
                        await self._unknown()
                return
            async with self.lock:
                permitted = False
                if (not self.inspection_only and not self.closing and not self.unsafe
                        and self.form_ready and self.form_scope_check() and not self.intent_attempted and not self.allowed_posts
                        and request.method == 'POST' and request.url == LEAD_URL and await self.submit_observed()):
                    try:
                        permitted = request.post_data_json == self.expected
                    except Exception:
                        permitted = False
                if not permitted:
                    self.blocked_writes += 1
                    await self._unknown()
                    try:
                        await route.abort()
                    except Exception:
                        self.unsafe = True
                    return
                # Set the local uncertainty fence before invoking persistence:
                # a callback can commit and then fail to return its acknowledgement.
                self.intent_attempted = True
                try:
                    acknowledged = await _callback(self.before_submit, copy.deepcopy(self.expected))
                    if acknowledged is not None and not isinstance(acknowledged, dict):
                        raise ValueError('提交意图持久化未确认')
                    await self.emit('submission_intent')
                    self.request = request
                    self.allowed_posts = 1
                    await route.continue_()
                except Exception:
                    self.blocked_writes += 1
                    await self._unknown()
                    try:
                        await route.abort()
                    except Exception:
                        self.unsafe = True
        except asyncio.CancelledError:
            self.unsafe = True
            raise
        except Exception:
            self.blocked_writes += 1
            await self._unknown()
            try:
                await route.abort()
            except Exception:
                self.unsafe = True
        finally:
            self.routes.discard(task)

    async def response(self, response):
        # Request identity, rather than an unbound success toast or URL alone,
        # binds this receipt to the one request permitted above.
        if self.request is None or response.request is not self.request or self.receipt_attempted:
            return
        self.receipt_attempted = True
        try:
            if response.url != LEAD_URL or type(response.status) is not int or not 200 <= response.status < 300:
                raise ValueError()
            body = await response.json()
            intake = body.get('intake_uid') if isinstance(body, dict) else None
            if (not isinstance(body, dict) or body.get('ok') is not True or body.get('persisted') is not True
                    or not isinstance(intake, str) or not re.fullmatch(r'lead-[A-Za-z0-9_-]{3,100}', intake)):
                raise ValueError()
            acknowledged = await _callback(self.save_receipt, body, response.status)
            if acknowledged is not None and not isinstance(acknowledged, dict):
                raise ValueError('回执持久化未确认')
            self.receipt_saved = True
            await self.emit('submitted_pending_review')
        except Exception:
            await self._unknown()

    def observe_response(self, response):
        task = asyncio.create_task(self.response(response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def observe_request_failure(self, request):
        if request is self.request:
            task = asyncio.create_task(self._unknown())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def result(self, *, normal_close_confirmed=False):
        positive = bool(normal_close_confirmed and not self.inspection_only and self.route_guard_installed
                        and not self.unsafe and not self.intent_attempted and not self.allowed_posts and not self.blocked_writes)
        stage = ('inspection_closed' if self.inspection_only else 'submitted_pending_review' if self.receipt_saved
                 else 'closed_not_submitted' if positive else 'submission_unknown')
        return {'stage': stage, 'message': MESSAGES[stage], 'window_open': False,
                'inspection_only': self.inspection_only, 'normal_close_confirmed': positive,
                'route_guard_installed': self.route_guard_installed, 'allowed_posts': self.allowed_posts,
                'blocked_writes': self.blocked_writes}


async def _unique_visible(locator):
    if await locator.count() != 1 or not await locator.is_visible():
        raise ValueError('目录控件不唯一或不可见，请本人核对')
    return locator


async def readback_form(form, fields):
    shown = {key: await form.locator('input[name="' + key + '"]').input_value()
             for key in ('name', 'url', 'rate', 'source', 'contact', 'notes')}
    selected = await _unique_visible(form.get_by_label(fields['models'][0], exact=True))
    if not await selected.is_checked() or await form.locator('input[type="checkbox"]:checked').count() != 1:
        raise ValueError('目录模型选项回读不一致')
    shown['models'] = [fields['models'][0]]
    if shown != fields:
        raise ValueError('目录七字段逐字回读不一致')


async def prepare_form(page, fields):
    if page.url != ENTRY_URL:
        raise ValueError('当前不是固定官方目录填写入口')
    if not isinstance(fields.get('models'), list) or len(fields['models']) != 1:
        raise ValueError('尚未核对多模型提交，当前只能选择一个已核对选项')
    form = page.locator('input[name="name"]').locator('xpath=ancestor::form')
    await form.wait_for(state='visible', timeout=1500)
    await _unique_visible(form)
    controls = {}
    for key in ('name', 'url', 'rate', 'source', 'contact', 'notes'):
        field = await _unique_visible(form.locator('input[name="' + key + '"]'))
        if not await field.is_editable() or not await field.evaluate("el => el.tagName === 'INPUT' && ['text','url'].includes(el.type)"):
            raise ValueError('目录只允许已核对的普通文本输入框')
        prior = await field.input_value()
        if prior and prior != fields[key]:
            raise ValueError('目录已有不同公开字段，保留原内容且不会覆盖')
        controls[key] = (field, prior)
    model = await _unique_visible(form.get_by_label(fields['models'][0], exact=True))
    checked = await form.locator('input[type="checkbox"]:checked').count()
    if checked and (checked != 1 or not await model.is_checked()):
        raise ValueError('目录已有其它模型选择，保留原内容且不会覆盖')
    for key, (field, prior) in controls.items():
        if prior != fields[key]:
            await field.fill(fields[key])
    if not await model.is_checked():
        await model.check()
    await readback_form(form, fields)
    return form


async def open_session(record, profile, executable_path, *, fallback_executable_path=None,
                       progress=None, before_submit=None, save_receipt=None, check_requested=None,
                       close_requested=None, playwright_factory=None, inspection_only=False, timeout_seconds=900):
    from playwright.async_api import async_playwright
    from services.browser_runtime import launch_context
    from services.geo_test_runner import exclusive_lock
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError('目录窗口等待时间无效')
    profile = Path(profile)
    # A per-session directory prevents restored tabs making requests between
    # persistent-context launch and installation of its route handler.
    for candidate in (profile, profile.with_name(profile.name + '-chrome')):
        if candidate.exists():
            metadata = candidate.lstat()
            if (not candidate.is_dir() or candidate.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & 0x400
                    or any(candidate.iterdir())):
                raise ValueError('目录申请需要全新专用会话目录，不会恢复旧浏览器页面')
    fields = copy.deepcopy(record.get('fields'))
    expected = None if inspection_only else expected_transport(fields)
    if not inspection_only and (record.get('submit_intent') or record.get('stage') not in ('opening', 'reviewed')
                                or before_submit is None or save_receipt is None):
        raise ValueError('目录申请未处于本次可提交会话，只能只读核对')
    state = SubmissionGuard(expected, before_submit=before_submit, save_receipt=save_receipt,
                            progress=progress, inspection_only=inspection_only)
    closed = asyncio.Event()
    controlled_close = False
    cleanup_succeeded = False
    cleanup = False
    cancelled = False
    context = None
    form = None
    scope_left = False
    navigation_changed = asyncio.Event()
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    def page_closed(*_):
        closed.set()

    def lost_control(*_):
        if not cleanup:
            state.unsafe = True
            closed.set()

    try:
        with exclusive_lock(profile / '.geo-test.lock'):
            async with (playwright_factory or async_playwright)() as playwright:
                options = {'headless': False, 'accept_downloads': False, 'viewport': None, 'no_viewport': True,
                           'args': ['--start-maximized'], 'chromium_sandbox': True, 'service_workers': 'block'}
                if executable_path:
                    options['executable_path'] = executable_path
                context = await launch_context(playwright, profile, options, fallback_executable_path)
                context.on('close', lost_control)
                context.on('response', state.observe_response)
                context.on('requestfailed', state.observe_request_failure)
                browser = context.browser
                if browser is not None:
                    browser.on('disconnected', lost_control)
                # The context-wide guard precedes every explicit navigation,
                # including inspection-only navigation and any later popup.
                await context.route('**/*', state.route)
                state.route_guard_installed = True
                page = context.pages[0] if context.pages else await context.new_page()
                state.form_scope_check = lambda: page.url == ENTRY_URL and not scope_left and not page.is_closed()
                binding = '__geo_directory_submit_' + uuid.uuid4().hex
                marker = '__geo_directory_submit_seen_' + uuid.uuid4().hex
                def form_submitted(source):
                    if (source.get('page') is page and source.get('frame') is page.main_frame
                            and page.url in (ENTRY_URL, LIST_URL)):
                        state.form_submit_seen = True
                await context.expose_binding(binding, form_submitted)
                await context.add_init_script(script=submit_observer_script(binding,marker))
                async def read_submit_signal():
                    if page.url not in (ENTRY_URL, LIST_URL):
                        return False
                    return await page.evaluate('(key) => window[key] === true',marker)
                state.submit_signal_read = read_submit_signal
                page.on('close', page_closed)
                page.on('crash', lost_control)
                def navigated(frame):
                    nonlocal scope_left
                    if getattr(frame, 'parent_frame', None) is None and getattr(frame, 'url', '') != ENTRY_URL:
                        if form is not None:
                            scope_left = True
                        state.form_ready = False
                        if getattr(frame, 'url', '') not in (ENTRY_URL, LIST_URL):
                            state.form_submit_seen = False
                        navigation_changed.set()
                page.on('framenavigated', navigated)
                try:
                    await state.emit('inspection_open' if inspection_only else 'opening')
                    await _owned_operation(page.goto(LIST_URL if inspection_only else ENTRY_URL,
                        wait_until='domcontentloaded', timeout=45000), closed, close_requested, state)
                    waiting_form = not inspection_only
                    while not closed.is_set():
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError()
                        if close_requested is not None and close_requested.is_set():
                            close_requested.clear()
                            state.closing = True
                            if not state.unsafe and not page.is_closed() and (browser is None or browser.is_connected()):
                                # Fixed no-data roundtrip proves control before
                                # issuing the explicit close, without auth DOM reads.
                                controlled_close = await page.evaluate('() => true') is True
                            break
                        poll_form = False
                        if waiting_form and not state.unsafe and not state.intent_attempted and not scope_left:
                            visible = False
                            if page.url == ENTRY_URL:
                                try:
                                    await page.locator('input[name="name"]').locator('xpath=ancestor::form').wait_for(
                                        state='visible', timeout=min(1000, max(1, int(remaining * 1000))))
                                    visible = True
                                except Exception:
                                    pass
                            if closed.is_set() or (close_requested is not None and close_requested.is_set()):
                                continue
                            if visible:
                                waiting_form = False
                                try:
                                    prepared, value = await _owned_operation(prepare_form(page, fields), closed, close_requested, state)
                                    if not prepared:
                                        continue
                                    form = value
                                except Exception:
                                    await state.emit('needs_manual_review')
                                else:
                                    await state.emit('awaiting_human_confirmation')
                                    state.form_ready = not scope_left and page.url == ENTRY_URL
                            else:
                                poll_form = True
                                if state.stage != 'awaiting_human_verification':
                                    await state.emit('awaiting_human_verification')
                        waiters = [asyncio.create_task(closed.wait())]
                        if check_requested is not None:
                            waiters.append(asyncio.create_task(check_requested.wait()))
                        if close_requested is not None:
                            waiters.append(asyncio.create_task(close_requested.wait()))
                        waiters.append(asyncio.create_task(navigation_changed.wait()))
                        if poll_form:
                            waiters.append(asyncio.create_task(asyncio.sleep(min(1, remaining))))
                        try:
                            done, _ = await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                            if not done:
                                raise asyncio.TimeoutError()
                        finally:
                            for task in waiters:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*waiters, return_exceptions=True)
                        if not closed.is_set() and check_requested is not None:
                            if check_requested.is_set():
                                check_requested.clear()
                                if not inspection_only and not state.intent_attempted and not state.unsafe and not scope_left:
                                    if form is None:
                                        waiting_form = True
                                    else:
                                        try:
                                            if page.url != ENTRY_URL:
                                                raise ValueError()
                                            await readback_form(form, fields)
                                        except Exception:
                                            state.form_ready = False
                                            await state.emit('needs_manual_review')
                                        else:
                                            await state.emit('awaiting_human_confirmation')
                                            state.form_ready = not scope_left and page.url == ENTRY_URL
                        if navigation_changed.is_set():
                            navigation_changed.clear()
                            if scope_left and not state.receipt_saved:
                                await state.emit('needs_manual_review')
                except asyncio.CancelledError:
                    state.unsafe = True
                    cancelled = True
                except Exception:
                    state.unsafe = True
                finally:
                    cleanup = True
                    state.closing = True
                    try:
                        await context.close()
                        cleanup_succeeded = True
                        pending = [task for task in state.tasks | state.routes if task is not asyncio.current_task() and not task.done()]
                        if pending:
                            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)
                    except Exception:
                        state.unsafe = True
    except asyncio.CancelledError:
        state.unsafe = True
        cancelled = True
    except Exception:
        state.unsafe = True
    finally:
        if context is not None and not cleanup:
            cleanup = True
            state.closing = True
            try:
                await context.close()
            except Exception:
                state.unsafe = True
    result = state.result(normal_close_confirmed=controlled_close and cleanup_succeeded and not cancelled)
    # Positive no-send evidence is returned only after context, Playwright and
    # profile lock have all closed normally; progress never carries that token.
    try:
        await state.emit(result['stage'], window_open=False)
    except Exception:
        result = state.result(normal_close_confirmed=False)
    if cancelled:
        raise asyncio.CancelledError()
    return result
