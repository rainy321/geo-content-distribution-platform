"""One visible, reviewed Toutiao article; normal manual verification only."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from urllib.parse import urlsplit


class ArticleSessionStopped(RuntimeError):
    pass


def article_text(content):
    title = str(content.title or '').strip()
    if not title or len(title) > 30:
        raise ValueError('今日头条标题需为 1–30 字，请修改后重新审稿；不会自动截断')
    body = str(content.content or '').strip()
    if not body:
        raise ValueError('今日头条正文不能为空')
    tags = list(dict.fromkeys(str(tag).strip().lstrip('#') for tag in content.tags if str(tag).strip().lstrip('#')))
    return title, body + ('\n\n' + ' '.join('#' + tag for tag in tags) if tags else '')


def normalized_body(value):
    # Rich text editors may represent paragraph boundaries with one or two
    # newlines. Preserve every non-empty line and all of its actual text.
    return '\n'.join(line.strip() for line in str(value or '').replace('\r\n', '\n').replace('\u00a0', ' ').split('\n') if line.strip())


async def notify(progress, message):
    if progress:
        result = progress(message)
        if inspect.isawaitable(result):
            await result


def require_open(page, browser):
    if page.is_closed() or not browser.is_connected():
        raise ArticleSessionStopped('已关闭发布窗口，请人工核对是否提交；不会自动重发')


async def blocking_reason(page):
    location = urlsplit(page.url)
    if location.hostname and (location.hostname.endswith('sso.toutiao.com') or '/auth/page/login' in location.path):
        return '需要登录'
    for text in ('扫码登录', '手机号登录'):
        matches = page.get_by_text(text, exact=True)
        for index in range(await matches.count()):
            if await matches.nth(index).is_visible():
                return '需要登录'
    candidates = page.locator('[id*="captcha"], [class*="captcha"], iframe[src*="captcha"], [role="dialog"], [role="alert"]')
    for index in range(await candidates.count()):
        item = candidates.nth(index)
        if not await item.is_visible():
            continue
        label = await item.get_attribute('class') or ''
        element_id = await item.get_attribute('id') or ''
        if 'captcha' in (label + element_id).lower() or await item.get_attribute('src'):
            box = await item.bounding_box()
            viewport = page.viewport_size
            if not box or box['width'] < 40 or box['height'] < 30:
                continue
            if viewport and (box['x'] + box['width'] <= 0 or box['y'] + box['height'] <= 0
                             or box['x'] >= viewport['width'] or box['y'] >= viewport['height']):
                continue
            return '需要安全验证'
        text = await item.inner_text()
        if any(marker in text for marker in ('请完成验证', '安全验证', '拖动滑块', '请向右滑动')):
            return '需要安全验证'
        if any(marker in text for marker in ('账号异常', '账号已被封禁', '发布权限受限', '操作过于频繁')):
            raise ArticleSessionStopped('平台提示账号或频率限制，请人工处理；本次不会自动重发')
    return None


async def wait_for_verification(page, browser, progress):
    waiting = False
    while True:
        require_open(page, browser)
        reason = await blocking_reason(page)
        if not reason:
            if waiting:
                await notify(progress, '正常验证已结束，继续检查同一篇文章；不会重发')
            return
        if not waiting:
            await notify(progress, reason + '：请在当前发布窗口正常完成，保持窗口打开；关闭窗口可停止')
            waiting = True
        await asyncio.sleep(0.5)


async def editor_fields(page):
    titles = []
    locator = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="字符"]')
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible() and await item.is_editable():
            titles.append(item)
    bodies = []
    for frame in page.frames:
        locator = frame.locator('[contenteditable="true"], textarea[placeholder*="正文"]')
        for index in range(await locator.count()):
            item = locator.nth(index)
            if await item.is_visible() and await item.is_editable():
                bodies.append(item)
    if len(titles) > 1 or len(bodies) > 1:
        raise ArticleSessionStopped('标题或正文编辑器不唯一，请人工检查；未提交')
    return (titles[0], bodies[0]) if titles and bodies else None


async def read_body(field):
    return await field.evaluate('''el => {
        if (!el.isContentEditable) return el.value;
        // The observed empty Toutiao editor renders its prompt as a noneditable
        // widget. Accept only that exact empty scaffold; never strip user text
        // merely because it says "请输入正文" or ignore arbitrary descendants.
        if (el.matches('div.ProseMirror[contenteditable="true"]') && el.childNodes.length===1) {
            const p=el.firstChild;
            if (p.nodeType===Node.ELEMENT_NODE && p.tagName==='P' && p.attributes.length===0) {
                const nodes=[...p.childNodes], widgets=nodes.filter(n=>n.nodeType===Node.ELEMENT_NODE
                    && n.matches('span.syl-placeholder.ProseMirror-widget[ignoreel="true"][contenteditable="false"]'));
                if(widgets.length===1 && widgets[0].textContent==='请输入正文'
                    && !widgets[0].children.length && nodes.every(n=>n===widgets[0]
                        || n.nodeType===Node.TEXT_NODE && !n.textContent.trim()
                        || n.nodeType===Node.ELEMENT_NODE && n.tagName==='BR' && n.attributes.length===0)) return '';
            }
        }
        return el.innerText;
    }''')


async def verify_readback(fields, title, body):
    if (await fields[0].input_value()).strip() != title:
        raise ArticleSessionStopped('标题回读与已审稿内容不一致，已停止提交，请人工检查')
    if normalized_body(await read_body(fields[1])) != normalized_body(body):
        raise ArticleSessionStopped('正文回读与已审稿内容不一致，已停止提交，请人工检查')


async def ai_declaration_selected(page):
    control = await exact_declaration_control(page, '引用AI', required=False)
    return bool(control and await control.is_checked())


async def exact_declaration_control(page, text, *, required=True):
    """Require an exact visible label and a real native/ARIA checked control.

    A CSS class containing "checked" or a nearby text node is not evidence of
    consent. Opaque or duplicate widgets stop the publication for manual review.
    """
    labels = page.locator('label').filter(has_text=re.compile(r'^\s*' + re.escape(text) + r'\s*$'))
    visible = [labels.nth(index) for index in range(await labels.count()) if await labels.nth(index).is_visible()]
    if not visible and not required:
        return None
    if len(visible) != 1:
        raise ArticleSessionStopped(f'“{text}”声明控件不唯一或不可读取，请人工核对；未提交')
    controls = visible[0].locator('input[type="checkbox"],input[type="radio"],[role="checkbox"],[role="radio"]')
    if await controls.count() != 1:
        raise ArticleSessionStopped(f'“{text}”缺少唯一可回读的 checked 控件，请人工核对；未提交')
    control = controls.first
    # is_checked understands native checked and role=checkbox/radio aria-checked.
    # Explicitly reject ARIA mixed/missing state rather than treating it as false.
    role = await control.get_attribute('role')
    if role in ('checkbox', 'radio') and await control.get_attribute('aria-checked') not in ('true', 'false'):
        raise ArticleSessionStopped(f'“{text}”勾选状态不明确，请人工核对；未提交')
    aria = await control.get_attribute('aria-checked')
    if aria in ('true', 'false') and (aria == 'true') != await control.is_checked():
        raise ArticleSessionStopped(f'“{text}”原生与辅助勾选状态不一致，请人工核对；未提交')
    return control


async def set_exact_declaration(page, text, checked, *, required=True):
    control = await exact_declaration_control(page, text, required=required)
    if control is None:
        return None
    if await control.is_checked() != checked:
        if await control.is_disabled():
            raise ArticleSessionStopped(f'“{text}”目前不能更改，请人工核对；未提交')
        # Click only the uniquely bound visible label. Never force a hidden
        # input, guess at an icon, or toggle an unchecked option unnecessarily.
        label = page.locator('label').filter(has_text=re.compile(r'^\s*' + re.escape(text) + r'\s*$'))
        visible = [label.nth(index) for index in range(await label.count()) if await label.nth(index).is_visible()]
        if len(visible) != 1:
            raise ArticleSessionStopped(f'“{text}”点击前控件发生变化，请人工核对；未提交')
        await visible[0].click(timeout=3000)
    control = await exact_declaration_control(page, text, required=required)
    if control is not None and await control.is_checked() != checked:
        raise ArticleSessionStopped(f'“{text}”设置后回读不符，请人工核对；未提交')
    return checked


async def audit_declaration_inventory(page, *, before_changes=False):
    """Inspect real checked properties independently of label visibility.

    Missing/hidden labels must not turn a checked authorization into an assumed
    false value. Unbound/unknown checked widgets require manual review, never a
    guessed click. Known visible grants may be cleared by the exact-label path.
    """
    controls = await page.evaluate('''() => {
        const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
        const text=e=>(e?.textContent||'').replace(/\\s+/g,'').trim();
        return [...document.querySelectorAll('input[type="checkbox"],input[type="radio"],[role="checkbox"],[role="radio"]')].map(e=>{
            const native=e.matches('input[type="checkbox"],input[type="radio"]');
            const labels=native?[...e.labels||[]]:[e.closest('label')].filter(Boolean);
            const aria=e.getAttribute('aria-checked');
            const names=labels.map(text).filter(Boolean);
            const labelled=(e.getAttribute('aria-labelledby')||'').split(/\\s+/).filter(Boolean).map(id=>document.getElementById(id));
            const name=names.length===1?names[0]:(e.getAttribute('aria-label')||labelled.map(text).join('')||(!native?text(e):'')).replace(/\\s+/g,'');
            const checked=native?e.checked:aria==='true'?true:aria==='false'?false:null;
            return {name,checked,label_count:labels.length,visible_label:labels.length===1&&visible(labels[0]),
                contradictory:native&&aria!==null&&(aria!=='true'&&aria!=='false'||(aria==='true')!==e.checked)};
        });
    }''')
    off = {'头条首发', '授权平台自动维权', '声明原创', '原创', '原创声明', '发布得更多收益', '投放广告赚收益'}
    allowed_checked = {'引用AI', '不投放广告', '无封面', '单图', '三图'}
    for control in controls:
        name, checked = control.get('name', ''), control.get('checked')
        is_donation = bool(re.fullmatch(r'允许赞赏(?:（今日还有\d+次机会）)?', name))
        if control.get('contradictory') or checked not in (True, False):
            raise ArticleSessionStopped('发现勾选状态不明确的声明或授权控件，请人工核对；未提交')
        if not checked:
            continue
        if control.get('label_count', 0) > 1:
            raise ArticleSessionStopped('已勾选声明关联多个label，请人工核对；未提交')
        if name in off or is_donation:
            if before_changes and control.get('visible_label'):
                continue
            raise ArticleSessionStopped('仍有隐藏或未明确取消的额外授权，请人工核对；未提交')
        if name not in allowed_checked:
            raise ArticleSessionStopped('发现未识别或未绑定label的已勾选声明，请人工核对；未提交')


async def enforce_article_permissions(page):
    """AI writing grants no first/original, enforcement, donation or repost opt-in.

    Labels come from the observed editor/screenshot. Mandatory controls missing
    from a new layout stop the session instead of assuming default consent is off.
    No fallback clicks by partial text or CSS checked classes are permitted.
    """
    await audit_declaration_inventory(page, before_changes=True)
    labels = page.locator('label')
    texts = []
    section_labels = set()
    for index in range(await labels.count()):
        item = labels.nth(index)
        if await item.is_visible():
            text = (await item.inner_text()).strip()
            texts.append(text)
            if not await item.locator('input[type="checkbox"],input[type="radio"],[role="checkbox"],[role="radio"]').count():
                if text in ('声明首发', '同时发布微头条', '作品声明', '发文特权', '投放广告'):
                    section_labels.add(text)
    # First turn off nested rights before first-publication hides that section.
    for name in ('授权平台自动维权', '声明原创', '原创', '原创声明'):
        await set_exact_declaration(page, name, False, required=False)
    await set_exact_declaration(page, '头条首发', False)
    headings = page.get_by_text('同时发布微头条', exact=True)
    if sum([await headings.nth(i).is_visible() for i in range(await headings.count())]) != 1:
        raise ArticleSessionStopped('未确认唯一“同时发布微头条”区域，请人工核对同步设置；未提交')
    await set_exact_declaration(page, '发布得更多收益', False)
    await set_exact_declaration(page, '不投放广告', True)
    if await set_exact_declaration(page, '投放广告赚收益', False) is None:
        raise ArticleSessionStopped('广告收益设置无法回读，请人工核对；未提交')
    donations = [text for text in texts if re.fullmatch(r'允许赞赏(?:（今日还有\d+次机会）)?', re.sub(r'\s+', '', text))]
    if len(donations) != 1:
        raise ArticleSessionStopped('赞赏设置缺少唯一已知控件，请人工核对；未提交')
    await set_exact_declaration(page, donations[0], False)
    known = {'授权平台自动维权', '声明原创', '原创', '原创声明', '头条首发', '发布得更多收益'}
    for text in texts:
        if re.search(r'原创|首发|维权|同步|微头条', text) and text not in known and text not in section_labels:
            raise ArticleSessionStopped('发现尚未识别的原创、首发或同步声明，请人工核对；未提交')
    await set_exact_declaration(page, '引用AI', True)
    if not await ai_declaration_selected(page):
        raise ArticleSessionStopped('“引用AI”声明未确认选中，请人工核对；未提交')
    await audit_declaration_inventory(page)
    return {'first_publication': False, 'original': False, 'automatic_rights': False,
            'micro_repost': False, 'advertising': False, 'donation': False, 'ai_reference': True}


async def decline_observed_sync_authorization(page):
    """Only cancel the observed cross-product authorization; never accept it.

    The screenshot proves its text, not a current DOM implementation. A missing
    standard dialog role/unique cancel button therefore stops for manual review.
    """
    title = page.get_by_text('作品同步授权', exact=True)
    if not any([await title.nth(i).is_visible() for i in range(await title.count())]):
        return False
    dialogs = page.get_by_role('dialog')
    matches = []
    for index in range(await dialogs.count()):
        dialog = dialogs.nth(index)
        if not await dialog.is_visible():
            continue
        heading = dialog.get_by_text('作品同步授权', exact=True)
        if sum([await heading.nth(i).is_visible() for i in range(await heading.count())]) == 1:
            matches.append(dialog)
    if len(matches) != 1:
        raise ArticleSessionStopped('作品同步授权弹窗结构未核验，请人工点取消；软件不会同意授权')
    dialog = matches[0]
    text = re.sub(r'\s+', '', await dialog.inner_text())
    if not all(anchor in text for anchor in ('开启作品同步后', '公开作品', '懂车帝', '住小帮', '如果你不希望自己的作品同步到上述平台')):
        raise ArticleSessionStopped('作品同步授权文案发生变化，请人工点取消；软件不会同意授权')
    buttons = dialog.get_by_role('button', name='取消', exact=True)
    visible = [buttons.nth(i) for i in range(await buttons.count()) if await buttons.nth(i).is_visible()]
    if len(visible) != 1 or not await visible[0].is_enabled():
        raise ArticleSessionStopped('作品同步授权缺少唯一取消按钮，请人工处理；软件不会同意授权')
    await visible[0].click(timeout=3000)
    await dialog.wait_for(state='hidden', timeout=3000)
    return True


async def run_visible_article(content, account_file, progress=None, *, playwright_factory=None, record_submission=None):
    from playwright.async_api import async_playwright
    from uploader.toutiao_uploader.main import LOCAL_CHROME_PATH, TOUTIAO_ARTICLE_URL, TouTiaoArticle
    from services.toutiao_publish_flow import click_exact_publish_and_observe
    from services.desktop_geo_jobs import edge_executable, chrome_executable

    title, body = article_text(content)
    submission = {'submission_phase': 'preparing', 'submission_attempted': False, 'content_write_attempted': False}
    async def record(phase):
        submission['submission_phase'] = phase
        if record_submission:
            value = record_submission(dict(submission))
            if inspect.isawaitable(value):
                await value
    async def stop_result(message):
        attempted = submission['submission_attempted']
        try:
            await record('submission_result_unknown' if attempted else 'stopped_before_submission')
        except Exception:
            logging.exception('Could not persist Toutiao submission phase; manual platform review required')
        return {'status': 'processing' if attempted else 'need_action', 'success': False,
                'message': ('已开始提交，平台最终状态未知；请到后台核对，软件不会自动重发' if attempted else message),
                **submission}
    await record('preparing')
    await notify(progress, '正在打开可见的今日头条发布窗口；本次只处理这一篇已审稿文章')
    async with (playwright_factory or async_playwright)() as playwright:
        options = {'headless': False}
        executable = LOCAL_CHROME_PATH or edge_executable() or chrome_executable()
        if executable:
            options['executable_path'] = executable
        browser = await playwright.chromium.launch(**options)
        context = None
        page = None
        try:
            context = await browser.new_context(storage_state=str(account_file), locale='zh-CN', viewport={'width': 1440, 'height': 940})
            page = await context.new_page()
            await page.goto(TOUTIAO_ARTICLE_URL, wait_until='domcontentloaded', timeout=60000)
            await wait_for_verification(page, browser, progress)
            # A normal login may land on the dashboard. Navigate to the editor
            # only before any text or submission has been sent.
            if urlsplit(page.url).path != urlsplit(TOUTIAO_ARTICLE_URL).path:
                await page.goto(TOUTIAO_ARTICLE_URL, wait_until='domcontentloaded', timeout=60000)
                await wait_for_verification(page, browser, progress)
            await context.storage_state(path=str(account_file))
            fields = None
            for _ in range(80):
                await wait_for_verification(page, browser, progress)
                fields = await editor_fields(page)
                if fields:
                    break
                await asyncio.sleep(0.25)
            if not fields:
                raise ArticleSessionStopped('未找到可填写的标题和正文，请人工核对发布权限或页面变化；未提交')
            if (await fields[0].input_value()).strip() or normalized_body(await read_body(fields[1])):
                raise ArticleSessionStopped('发布页已有内容或自动保存草稿，已停止以免覆盖；请人工核对后重新发起')
            submission['article_permissions'] = await enforce_article_permissions(page)
            await record('additional_permissions_checked')
            await notify(progress, '正在填写已审稿标题、正文和文末标签，随后逐字回读')
            try:
                submission['content_write_attempted'] = True
                await record('content_write_attempted')
                await fields[0].fill(title)
                await fields[1].fill(body)
            except Exception:
                require_open(page, browser)
                if not await blocking_reason(page):
                    raise
                # A verification may interrupt a fill. Keep the page, then
                # inspect what actually landed; never repeat the write.
                await wait_for_verification(page, browser, progress)
            await verify_readback(fields, title, body)
            helper = TouTiaoArticle(title, body, [], 0, account_file,
                cover_path=content.images[0] if content.images else None, work_statements=['引用AI'])
            await wait_for_verification(page, browser, progress)
            await helper.handle_cover(page)
            if content.images and not await helper._cover_already_set(page):
                raise ArticleSessionStopped('封面未确认成功，请人工检查；未提交')
            submission['article_permissions'] = await enforce_article_permissions(page)
            async def verification():
                if submission['submission_attempted']:
                    try:
                        if await decline_observed_sync_authorization(page):
                            submission['cross_product_sync_authorization'] = 'declined'
                            await record('cross_product_sync_authorization_declined')
                    except ArticleSessionStopped:
                        submission['cross_product_sync_authorization'] = 'manual_review_required'
                        await record('cross_product_sync_authorization_unverified')
                        raise
                await wait_for_verification(page, browser, progress)
            await verification()
            await verify_readback(fields, title, body)
            submission['article_permissions'] = await enforce_article_permissions(page)
            await record('ready_to_submit')
            await notify(progress, '回读核对通过，正在提交这一篇文章；不会自动重试')
            async def attempted():
                submission['submission_attempted'] = True
                await record('initial_click_attempted')
            result = await click_exact_publish_and_observe(page,
                before_confirmation=verification, after_submission=verification, on_submission_attempt=attempted)
            await record('result_observed')
            await context.storage_state(path=str(account_file))
            await notify(progress, result.get('message') or '提交已观察，等待平台结果')
            result.update(submission)
            return result
        except ArticleSessionStopped as error:
            return await stop_result(str(error))
        except asyncio.CancelledError:
            return await stop_result('用户已停止发布，请人工核对；不会自动重发')
        except Exception:
            if submission['submission_attempted']:
                return await stop_result('提交结果未知')
            if page is not None and (page.is_closed() or not browser.is_connected()):
                return await stop_result('发布窗口已关闭，尚未开始提交；可检查草稿后重新发起')
            raise
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                await browser.close()
            except Exception:
                pass
