"""Ordinary QCC UI assistance, ending before the owner's final submission.

Only public company/proposal text is filled. No contact values, screenshots,
raw page text, uploads, CAPTCHA actions, payment or final-submit clicks are saved.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from services.source_maintenance_service import ENTRY_URL


async def _unique_visible(locator):
    values = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            values.append(candidate)
    if len(values) != 1:
        raise ValueError('当前控件尚未就绪或存在歧义，请在窗口核对；不会重复填写')
    return values[0]


async def _shown(locator):
    return any([await locator.nth(index).is_visible() for index in range(await locator.count())])


def _allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname == 'help.qcc.com'
            and not parsed.username and not parsed.password and parsed.port in (None, 443)
            and parsed.path in ('/spt/support/handle-dissent', '/spt/support/handle-dissent/data',
                                '/spt/support/handle-dissent/verify'))


async def observe(page, record, *, bound=False):
    # Text is ephemeral: only fixed messages/booleans leave this function.
    body = await page.locator('body').inner_text(timeout=5000)
    if any(text in body for text in ('此浏览器或应用可能不安全', 'This browser or app may not be secure')):
        return {'stage': 'browser_rejected', 'message': '平台拒绝当前浏览器。请本人在日常浏览器打开官方入口处理；软件不会绕过限制或搬移登录会话。'}
    challenge = page.locator('iframe[src*="captcha"],iframe[src*="verify"],[class*="captcha-verify"],[id*="captcha"]')
    if any(text in body for text in ('请拖动滑块完成拼图', '请完成安全验证', '关闭验证', '刷新验证')) or await _shown(challenge):
        return {'stage': 'waiting_verification', 'message': '企查查需要正常安全验证，请在当前窗口完成；保留同一次申请，不要重复点击提交。'}
    if await _shown(page.get_by_text('扫码登录', exact=True)):
        return {'stage': 'awaiting_login', 'message': '请在企查查专用窗口正常登录，完成后保持窗口打开，再点击检查当前窗口。'}
    if not _allowed(page.url):
        return {'stage': 'needs_manual_review', 'message': '当前页面不是已确认的企查查补正页面，请本人核对；软件不会导航到自定义入口。'}
    if await _shown(page.get_by_text(re.compile(r'^(?:提交成功|申请已提交|提交完成|申请成功)[！!。\s]*$'))):
        if bound:
            return {'stage': 'submitted_pending_review', 'submission_success_observed': True,
                    'message': '已看到同一申请提交成功提示，现为待审核；尚未证明公开资料更新或搜索采用。'}
        return {'stage': 'needs_manual_review', 'message': '页面出现提交提示，但尚不能绑定到本任务；保留原申请，不能据此重提或标为本任务成功。'}
    if await _shown(page.get_by_role('button', name=re.compile(r'^(?:立即支付|确认支付|购买|立即开通)$'))):
        return {'stage': 'fee_required_stopped', 'message': '当前页面要求付费，软件已停止；请核对实际权益，不会购买或继续申请。'}
    if urlsplit(page.url).path.endswith('/verify'):
        if record.get('read_only') or bound:
            return {'stage': ('submitted_pending_review' if record['stage'] == 'submitted_pending_review' else 'submission_unknown'),
                'message': '原申请的确认页仍在，不代表尚未提交。请勿重复确认；当前没有新的可绑定回执时，只能正常查看平台通知或人工核对。'}
        if (record['company_name'] in body and record['module_label'] in body
                and record['proposal'] and record['proposal'] in body):
            return {'stage': 'confirmation_ready', 'confirmation_seen': True,
                'message': '公开说明已回读一致。请本人在官方页填写姓名和联系方式、检查并正常确认提交；软件不会代填或点击最终提交。'}
        return {'stage': 'needs_manual_review', 'message': '确认页的公司、模块或公开说明无法完整匹配，请本人核对原申请；不会提交或重新填写。'}
    if record.get('read_only'):
        return {'stage': 'submitted_pending_review' if record['stage'] == 'submitted_pending_review' else 'needs_manual_review',
            'message': '原任务只读核对。当前页面没有可确认的原申请回执，请本人正常查看平台通知；软件不会猜测查询入口或重新填写申请。'}
    return {'stage': 'needs_manual_input', 'message': '请查看当前企查查页面；软件只处理已核对的公司官网和简介，不处理工商变更、证明上传或最终提交。'}


async def prepare_form(page, record, *, before_write):
    if record.get('read_only'):
        raise ValueError('原任务已填写或提交状态未知，只能检查，不能重新准备申请')
    initial = await observe(page, record)
    if initial['stage'] != 'needs_manual_input' or not _allowed(page.url):
        return initial
    company = await _unique_visible(page.get_by_placeholder('请输入企业名称', exact=True))
    current = await company.input_value()
    if current and current != record['company_name']:
        return {'stage': 'needs_manual_review', 'message': '当前页面已有其他企业信息，已保留；请本人核对，不覆盖原申请。'}
    await company.fill(record['company_name'])
    await company.click()
    candidate = page.locator('em').filter(has_text=re.compile('^' + re.escape(record['company_name']) + '$'))
    await candidate.first.wait_for(state='visible', timeout=12000)
    await page.wait_for_timeout(300)  # Allow the normal dropdown animation to settle.
    selected = await _unique_visible(candidate)
    await selected.click()
    await page.get_by_text('隐藏无数据模块', exact=True).wait_for(state='visible', timeout=10000)
    if await company.input_value() != record['company_name']:
        raise ValueError('所选公司回读不一致，已停止')
    module = await _unique_visible(page.get_by_text(record['module_label'], exact=True))
    await module.click()
    next_step = await _unique_visible(page.get_by_text(re.compile(
        r'^(?:已选' + re.escape(record['module_label']) + r'，)?下一步$')))
    if not await next_step.is_enabled():
        raise ValueError('当前模块尚不可继续，请本人核对')
    await next_step.click()
    field = page.locator('textarea:visible')
    await field.first.wait_for(state='visible', timeout=12000)
    field = await _unique_visible(field)
    body = await page.locator('body').inner_text(timeout=5000)
    if record['company_name'] not in body or record['module_label'] not in body:
        raise ValueError('当前填写页不能绑定所选公司和模块，已停止')
    prior = await field.input_value()
    if prior and prior != record['proposal']:
        return {'stage': 'needs_manual_review', 'message': '当前申请已有不同公开说明，已保留；软件不会覆盖或重复准备。'}
    # Persist the irreversible-on-reopen fence before any proposal input.
    before_write()
    if not prior:
        await field.fill(record['proposal'])
    if await field.input_value() != record['proposal']:
        raise ValueError('公开说明逐字回读不一致，已停止；不会再次填写')
    preview = await _unique_visible(page.get_by_text('下一步', exact=True))
    if not await preview.is_enabled():
        return {'stage': 'needs_manual_input', 'message': '平台要求补充材料或其他信息，请本人在原页面处理；软件不会上传材料。'}
    await preview.click()
    await page.wait_for_url(re.compile(r'https://help\.qcc\.com/spt/support/handle-dissent/verify(?:\?.*)?$'), timeout=15000)
    return await observe(page, record)


async def wait_initial_ready(page):
    """Wait for the SPA's known normal form or manual-action UI, without clicks."""
    candidates = [page.get_by_placeholder('请输入企业名称', exact=True).first,
                  page.get_by_placeholder('请填写您的姓名', exact=True).first,
                  page.get_by_text('扫码登录', exact=True).first,
                  page.get_by_text('请拖动滑块完成拼图', exact=True).first,
                  page.locator('iframe[src*="captcha"]:visible,iframe[src*="verify"]:visible').first]
    waiters = [asyncio.create_task(item.wait_for(state='visible', timeout=15000)) for item in candidates]
    try:
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        # Individual selector timeouts do not authorize filling or closing.
        if not any(task.exception() is None for task in done):
            raise ValueError('已知企查查界面尚未就绪')
    finally:
        for task in waiters:
            if not task.done():
                task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


async def open_session(record, profile, executable_path, *, fallback_executable_path=None,
                       progress=None, check_requested=None, playwright_factory=None):
    from playwright.async_api import async_playwright
    from services.browser_runtime import launch_context
    from services.geo_test_runner import exclusive_lock
    closed = asyncio.Event()
    latest = record.copy()
    bound = False
    wrote = bool(record.get('write_attempted'))
    preparation_failed = False

    def emit(value, *, window_open=True):
        nonlocal latest, bound
        if value.get('confirmation_seen'):
            bound = True
        latest.update(value, write_attempted=wrote, window_open=window_open)
        if progress:
            progress({**value, 'write_attempted': wrote, 'window_open': window_open,
                      'inspection_only': preparation_failed})

    with exclusive_lock(profile / '.geo-test.lock'):
        async with (playwright_factory or async_playwright)() as playwright:
            options = {'headless': False, 'accept_downloads': False, 'viewport': None}
            if executable_path:
                options['executable_path'] = executable_path
            context = await launch_context(playwright, profile, options, fallback_executable_path)
            context.on('close', lambda *_: closed.set())
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                page.on('close', lambda *_: closed.set())
                page.on('crash', lambda *_: closed.set())
                try:
                    if not _allowed(page.url):
                        await page.goto(ENTRY_URL, wait_until='domcontentloaded', timeout=45000)
                    await wait_initial_ready(page)
                except Exception:
                    if page.is_closed():
                        closed.set()
                    else:
                        preparation_failed = True
                        record = {**record, 'read_only': True}
                        emit({'stage': 'needs_manual_review', 'message': '企查查页面尚未就绪，请在当前窗口核对；窗口保持打开，软件不会自动重新填写。'})

                def before_write():
                    nonlocal wrote
                    if wrote:
                        raise ValueError('本任务已有公开说明写入尝试，不会再次填写')
                    wrote = True
                    emit({'stage': 'needs_manual_input', 'message': '本次公开说明写入意图已保存；关闭后仅检查原申请，不会自动重新填写。'})

                async def inspect(*, can_prepare):
                    nonlocal preparation_failed, record
                    try:
                        value = await observe(page, record, bound=bound)
                        if (can_prepare and not preparation_failed and not record.get('read_only')
                                and not wrote and value['stage'] == 'needs_manual_input'):
                            value = await prepare_form(page, record, before_write=before_write)
                        emit(value)
                    except Exception:
                        if page.is_closed():
                            closed.set()
                            return
                        preparation_failed = True
                        record = {**record, 'read_only': True}
                        emit({'stage': 'needs_manual_review', 'message': '当前控件未就绪、存在歧义或回读未通过。请在保留的窗口核对；检查只读，不会再次填写或提交。'})

                if not closed.is_set() and not preparation_failed:
                    await inspect(can_prepare=True)
                while not closed.is_set():
                    waiters = [asyncio.create_task(closed.wait())]
                    if check_requested is not None:
                        waiters.append(asyncio.create_task(check_requested.wait()))
                    if bound and latest['stage'] != 'submitted_pending_review':
                        # The platform's success toast can be brief. Watching
                        # visible DOM is read-only and never clicks submission.
                        waiters.append(asyncio.create_task(page.get_by_text(re.compile(
                            r'^(?:提交成功|申请已提交|提交完成|申请成功)[！!。\s]*$')).first.wait_for(state='visible', timeout=0)))
                    try:
                        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for waiter in waiters:
                            if not waiter.done():
                                waiter.cancel()
                        await asyncio.gather(*waiters, return_exceptions=True)
                    if closed.is_set():
                        break
                    if check_requested is not None:
                        check_requested.clear()
                    # Explicit checks are read-only after a write or fence.
                    await inspect(can_prepare=not preparation_failed and not wrote and not record.get('read_only'))
                stage = ('submitted_pending_review' if latest['stage'] == 'submitted_pending_review'
                         else 'submission_unknown' if wrote or record.get('read_only') else 'window_closed')
                emit({'stage': stage, 'message': ('原申请待审核，记录已保留。' if stage == 'submitted_pending_review' else
                    '窗口已结束，原申请与提交结果保留待核对；不会自动重新申请。')}, window_open=False)
                return latest
            finally:
                await context.close()
