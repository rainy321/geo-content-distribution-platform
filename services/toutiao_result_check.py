"""Read visible management/public pages; never write an editor or submit again."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from services.article_service import get_article
from services.publish_job_service import (get_publish_job, publish_authorization_matches,
    reconcile_publish_job_success, update_publish_job_progress)
from services.real_publisher_factory import RealPublisherFactory
from services.toutiao_article_session import article_text, normalized_body, wait_for_verification


MANAGEMENT_URL = 'https://mp.toutiao.com/profile_v4/manage/content/all?enter_from=left_menu'
HK = timezone(timedelta(hours=8))
_lock = threading.Lock()
_active = set()


class ResultCheckUnavailable(ValueError):
    pass


def _stamp(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def _article_id(url):
    try:
        parsed = urlsplit(url)
        match = re.fullmatch(r'/(?:item|article)/([1-9][0-9]*)/?', parsed.path)
        if (parsed.scheme == 'https' and parsed.hostname == 'www.toutiao.com'
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.fragment and match):
            return match.group(1)
    except (ValueError, TypeError):
        pass
    return None


def _load_binding(database, cookies_directory, job_id):
    job = get_publish_job(database, job_id)
    if job['demo'] or job['platform'] != 'toutiao' or job['status'] != 'processing':
        raise ResultCheckUnavailable('仅可核对正在处理的真实今日头条任务；不会重新提交')
    if not job.get('account_id') or not job.get('started_at'):
        raise ResultCheckUnavailable('任务缺少绑定账号或开始时间，需人工核对平台结果')
    article = get_article(database, job['article_id'])
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        if not publish_authorization_matches(job, article, connection=connection):
            raise ResultCheckUnavailable('已授权内容或目标配置发生变化，不能用当前稿件核对原发布结果')
        account = connection.execute('SELECT id,filePath FROM user_info WHERE id=? AND type=7 AND status=1',
                                     (job['account_id'],)).fetchone()
    if not account:
        raise ResultCheckUnavailable('任务绑定的头条账号不可用，请正常重新登录该账号后再核对')
    account_file = RealPublisherFactory(database, cookies_directory=cookies_directory)._safe_cookie_path(account['filePath'])
    if account_file is None or not account_file.is_file():
        raise ResultCheckUnavailable('任务绑定的正常登录会话不可用，请重新登录后再核对')
    directory = Path(database).resolve().parent / 'publish-evidence'
    journal_path = directory / f'job-{job_id}.json'
    try:
        if journal_path.is_symlink() or journal_path.stat().st_size > 1024 * 1024:
            raise ValueError('unsafe journal')
        raw = journal_path.read_bytes()
        journal = json.loads(raw)
        for field in ('article_id', 'account_id', 'authorization_fingerprint', 'started_at'):
            if journal[field] != job[field]:
                raise ValueError('different attempt')
        if journal['job_id'] != job_id or journal['platform'] != 'toutiao' or journal['submission_attempted'] is not True:
            raise ValueError('no submission receipt')
        started = _stamp(job['started_at'])
        times = [_stamp(journal['updated_at'])]
        for path in (directory / 'history' / f'job-{job_id}' / 'phases').glob('*.json'):
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
            phase = json.loads(path.read_bytes())
            if (phase.get('submission_attempted') is True and phase.get('job_id') == job_id
                    and all(phase.get(field) == job[field] for field in ('article_id','account_id','authorization_fingerprint','started_at'))):
                times.append(_stamp(phase['updated_at']))
        submitted = min(times)
        if submitted < started or submitted > datetime.now(timezone.utc) + timedelta(seconds=5):
            raise ValueError('invalid submission time')
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise ResultCheckUnavailable('缺少与本次任务绑定的已提交阶段证据，保留处理中，请人工核对') from error
    title, body = article_text(SimpleNamespace(title=article['title'], content=article['content'], tags=article.get('tags') or []))
    return {'job': job, 'article': article, 'account_file': account_file, 'title': title, 'body': body,
            'submitted_at': submitted, 'journal_sha256': _sha(raw), 'journal_path': journal_path}


def _time_for_card(label, submitted):
    if not re.fullmatch(r'\d{2}-\d{2} \d{2}:\d{2}', label or ''):
        return None
    # Card dates omit the year. Resolve only against this recorded attempt,
    # including New Year, and within a bounded immediate-publication window.
    choices = []
    for year in (submitted.astimezone(HK).year - 1, submitted.astimezone(HK).year, submitted.astimezone(HK).year + 1):
        try:
            stamp = datetime.strptime(f'{year}-{label}', '%Y-%m-%d %H:%M').replace(tzinfo=HK)
        except ValueError:
            continue
        if submitted - timedelta(seconds=60) <= stamp <= submitted + timedelta(minutes=30):
            choices.append(stamp)
    return choices[0] if len(choices) == 1 else None


MANAGEMENT_JS = r'''() => {
    const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
    return [...document.querySelectorAll('.article-card')].map((card,index)=>{
        const links=[...card.querySelectorAll('.title-wrap > a.title[href]')].filter(visible);
        const times=[...card.querySelectorAll('.title-wrap > .create-time')].filter(visible);
        return {index,visible:visible(card),title:links.length===1?links[0].innerText.trim():null,
            href:links.length===1?links[0].href:null,time:times.length===1?times[0].innerText.trim():null,
            states:[...card.querySelectorAll('.abstruct .byte-tag')].filter(visible).map(e=>e.innerText.trim()),
            dom:card.outerHTML};
    }).filter(card=>card.visible);
}'''


PUBLIC_JS = r'''() => {
    const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
    const roots=[...document.querySelectorAll('.article-content')].filter(visible);
    if(roots.length!==1) return {ready:false};
    const root=roots[0], heads=[...root.querySelectorAll('h1')].filter(visible);
    const bodies=[...root.querySelectorAll('article.tt-article-content')].filter(visible);
    const dates=[...root.querySelectorAll('.article-meta > span')].filter(visible)
        .map(e=>e.innerText.trim()).filter(t=>/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(t));
    const ai=[...root.querySelectorAll('.ai-meta')].filter(visible).map(e=>e.innerText.trim());
    let paragraphBody=null;
    if(bodies.length===1) {
        const paragraphs=[...bodies[0].childNodes];
        const inline=node=>node.nodeType===Node.TEXT_NODE ||
            (node.nodeType===Node.ELEMENT_NODE &&
                ((node.tagName==='BR' && !node.attributes.length && !node.childNodes.length)
                 || (['SPAN','A'].includes(node.tagName)
                    && getComputedStyle(node).display!=='none' && getComputedStyle(node).visibility!=='hidden'
                    && [...node.childNodes].every(inline))));
        if(paragraphs.length && paragraphs.every(p=>p.nodeType===Node.ELEMENT_NODE&&p.tagName==='P'
                && getComputedStyle(p).display!=='none'&&getComputedStyle(p).visibility!=='hidden'
                && [...p.childNodes].every(inline))) {
            // Observed public article layout inserts BR before inline URLs.
            // Preserve every paragraph's exact textContent; only reconstruct
            // paragraph boundaries, without deleting arbitrary text whitespace.
            paragraphBody=paragraphs.map(p=>p.textContent).join('\n\n');
        }
    }
    return {ready:heads.length===1&&bodies.length===1&&dates.length===1&&paragraphBody!==null,
        title:heads.length===1?heads[0].innerText.trim():null,
        body:bodies.length===1?bodies[0].innerText:null,time:dates.length===1?dates[0]:null,
        paragraph_body:paragraphBody,
        declaration:ai.length===1?ai[0]:null,dom:root.outerHTML};
}'''


async def _checkpoint(page, directory, name, payload):
    _write(directory / (name+'.json'), payload)
    await page.screenshot(path=str(directory / (name+'.png')), full_page=True)


async def _save_failure_pages(context,directory):
    """Capture already-open pages before cleanup, without navigation or writes."""
    for index,page in enumerate(context.pages[:6]):
        payload={'url':page.url}
        try:
            payload['body_dom']=await asyncio.wait_for(page.evaluate("() => document.body?.innerHTML ?? ''"),5)
            payload['public_snapshot']=await asyncio.wait_for(page.evaluate(PUBLIC_JS),5)
        except Exception as error:
            payload['snapshot_error_type']=type(error).__name__
        _write(directory/f'failure-page-{index}.json',payload)
        try:
            await page.screenshot(path=str(directory/f'failure-page-{index}.png'),full_page=True,timeout=5000)
        except Exception:
            pass


async def inspect_result_pages(page, context, binding, directory, *, wait_for_user, timeout_seconds=30, on_management_verified=None):
    """Normal management navigation and one observed article click; no writes."""
    await page.goto(MANAGEMENT_URL, wait_until='domcontentloaded', timeout=60000)
    await wait_for_user(page)
    if urlsplit(page.url).hostname != 'mp.toutiao.com':
        raise ResultCheckUnavailable('当前不是头条作品管理会话，请正常登录绑定账号')
    if urlsplit(page.url).path != urlsplit(MANAGEMENT_URL).path:
        await page.goto(MANAGEMENT_URL, wait_until='domcontentloaded', timeout=60000)
        await wait_for_user(page)
    cards = []
    deadline = time.monotonic()+timeout_seconds
    while time.monotonic() < deadline:
        await wait_for_user(page)
        cards = await page.evaluate(MANAGEMENT_JS)
        if cards:
            break
        await asyncio.sleep(.3)
    await _checkpoint(page,directory,'management',{'url':page.url,'cards':cards})
    matches = [card for card in cards if card['title'] == binding['title']]
    if any(not card['time'] or not re.fullmatch(r'\d{2}-\d{2} \d{2}:\d{2}',card['time']) for card in matches):
        raise ResultCheckUnavailable('同标题作品缺少可核对时间，不能唯一绑定本次发布')
    candidates = [card for card in matches if _time_for_card(card['time'],binding['submitted_at']) is not None]
    if len(candidates) != 1 or candidates[0]['states'].count('已发布') != 1:
        raise ResultCheckUnavailable('未找到唯一、时间匹配且已发布的作品；保留处理中，不会重发')
    selected = candidates[0]
    observed_id = _article_id(selected['href'])
    if not observed_id:
        raise ResultCheckUnavailable('作品卡未显示合法公开文章链接，未猜测网址')
    # Re-check the exact bound card before its one normal link click.
    current = await page.evaluate(MANAGEMENT_JS)
    if [card for card in current if card['index']==selected['index']] != [selected]:
        raise ResultCheckUnavailable('作品卡已变化，请重新只读核对；未点击其他作品')
    if on_management_verified:
        # Persist a normal authenticated management session before a public
        # article visit can refresh guest state; never save after public read.
        await on_management_verified()
    new_pages = []
    navigation_urls = []
    invalid_navigation = []
    def track(target):
        def navigated(frame):
            if frame != target.main_frame or frame.url in ('','about:blank'):
                return
            navigation_urls.append(frame.url)
            if _article_id(frame.url) != observed_id:
                invalid_navigation.append(frame.url)
        target.on('framenavigated',navigated)
    track(page)
    def opened(target):
        new_pages.append(target)
        track(target)
    context.on('page',opened)
    try:
        await page.locator('.article-card').nth(selected['index']).locator('.title-wrap > a.title[href]').click(no_wait_after=True, timeout=10000)
        deadline = time.monotonic()+timeout_seconds
        destination, public = None, None
        seen_urls = []
        while time.monotonic() < deadline:
            if invalid_navigation:
                raise ResultCheckUnavailable('点击后的页面离开该公开文章，未接受后续跳转结果')
            if len(new_pages)>1:
                raise ResultCheckUnavailable('点击后出现多个新页面，无法唯一绑定公开结果')
            target = new_pages[0] if new_pages else page
            location = target.url
            if location not in ('','about:blank',page.url if target is page and location==MANAGEMENT_URL else ''):
                if not seen_urls or seen_urls[-1]!=location:
                    seen_urls.append(location)
                if _article_id(location) != observed_id:
                    # Until a popup exists the management page may still be
                    # unchanged; any actual destination must keep article ID.
                    if target is not page or urlsplit(location).path != urlsplit(MANAGEMENT_URL).path:
                        raise ResultCheckUnavailable('公开页跳转与观察到的作品不一致，保留未知')
                else:
                    destination=target
                    try:
                        await wait_for_user(destination)
                        public=await destination.evaluate(PUBLIC_JS)
                    except Exception as error:
                        if 'Execution context was destroyed' not in str(error) and 'Cannot find context' not in str(error):
                            raise
                        await asyncio.sleep(.2)
                        continue
                    if public.get('ready'):
                        break
            await asyncio.sleep(.25)
        if destination is None or not public or not public.get('ready'):
            if destination is not None:
                await _checkpoint(destination,directory,'public-incomplete',{'url':destination.url,
                    'public_snapshot':public,'body_dom':await destination.evaluate("() => document.body?.innerHTML ?? ''")})
            raise ResultCheckUnavailable('公开正文未完整显示，不能核验发布结果')
        if invalid_navigation:
            raise ResultCheckUnavailable('公开页导航证据发生变化，未更新发布状态')
        await _checkpoint(destination,directory,'public',{'url':destination.url,'navigation_urls':navigation_urls or seen_urls,**public})
        published = datetime.strptime(public['time'],'%Y-%m-%d %H:%M').replace(tzinfo=HK)
        card_time = _time_for_card(selected['time'],binding['submitted_at'])
        plain_match=normalized_body(public['body'])==normalized_body(binding['body'])
        paragraphs_match=isinstance(public.get('paragraph_body'),str) and normalized_body(public['paragraph_body'])==normalized_body(binding['body'])
        if (public['title'] != binding['title'] or published != card_time
                or public.get('declaration') != '作品声明：内容由AI生成'
                or not (plain_match or paragraphs_match)
                or _article_id(destination.url) != observed_id):
            raise ResultCheckUnavailable('公开页标题、发布时间、AI声明或全文与已授权稿件不一致，保留处理中')
        return {'result_url':destination.url,'management_url':MANAGEMENT_URL,'observed_href':selected['href'],
            'platform_article_id':observed_id,'published_at':published.isoformat(),
            'title':public['title'],'body_sha256':_sha(normalized_body(public['body'] if plain_match else public['paragraph_body']).encode()),
            'body_comparison':('exact_nonempty_lines_crlf_nbsp_and_boundary_spaces_only' if plain_match
                               else 'observed_direct_p_inline_textcontent_exact_nonempty_lines'),
            'declaration':public['declaration'],'navigation_urls':navigation_urls or seen_urls}
    finally:
        context.remove_listener('page',opened)


async def check_toutiao_result(database, cookies_directory, job_id, *, playwright_factory=None, progress=None):
    binding = _load_binding(database,cookies_directory,job_id)
    identifier = 'check-'+datetime.now(HK).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    directory = Path(database).resolve().parent/'publish-evidence'/'result-checks'/f'job-{job_id}'/identifier
    directory.mkdir(parents=True,exist_ok=False)
    proof = {'schema':1,'kind':'read_only_platform_result_check','job_id':job_id,
        'article_id':binding['job']['article_id'],'account_id':binding['job']['account_id'],
        'authorization_fingerprint':binding['job']['authorization_fingerprint'],
        'started_at':binding['job']['started_at'],'submission_journal_sha256':binding['journal_sha256'],
        'original_job':binding['job'],'inspected_at':datetime.now(HK).isoformat(),
        'submission_attempted':False,'editor_write_attempted':False,'status':'checking'}
    _write(directory/'result.json',proof)
    def notify(message):
        if progress:
            progress('核对平台结果（只读）：'+message)
    try:
        if playwright_factory is None:
            from playwright.async_api import async_playwright
            playwright_factory=async_playwright
        from services.desktop_geo_jobs import edge_executable,chrome_executable
        async with playwright_factory() as playwright:
            options={'headless':False}
            executable=edge_executable() or chrome_executable()
            if executable:
                options['executable_path']=executable
            browser=await playwright.chromium.launch(**options)
            context=None
            try:
                context=await browser.new_context(storage_state=str(binding['account_file']),locale='zh-CN',viewport={'width':1440,'height':940})
                page=await context.new_page()
                async def manual(current):
                    await wait_for_verification(current,browser,notify)
                notify('正在打开作品管理；只检查这一篇文章，不填写、不再次提交')
                async def save_management_session():
                    await context.storage_state(path=str(binding['account_file']))
                    proof['management_session_saved_before_public_navigation']=True
                observed=await inspect_result_pages(page,context,binding,directory,wait_for_user=manual,
                    on_management_verified=save_management_session)
                current=_load_binding(database,cookies_directory,job_id)
                if (current['journal_sha256'] != binding['journal_sha256'] or current['account_file']!=binding['account_file'] or
                        any(current['job'][key]!=binding['job'][key] for key in ('article_id','account_id','started_at','authorization_fingerprint'))):
                    raise ResultCheckUnavailable('核对期间发布任务或提交阶段已变化，原证据保留，未更新结果')
                proof.update(status='verified',**observed)
                _write(directory/'result.json',proof)
                verification_path=directory/'verification.json'
                with verification_path.open('x',encoding='utf-8') as stream:
                    stream.write(json.dumps(proof,ensure_ascii=False,indent=2))
                reconciled=reconcile_publish_job_success(database,job_id,result_url=observed['result_url'],
                    message='只读核对通过：作品管理与公开页的标题、时间、AI声明和全文一致；未再次提交',
                    expected_binding={key:binding['job'][key] for key in ('article_id','account_id','started_at','authorization_fingerprint')},
                    platform_evidence={'published_at':observed['published_at'],'precision':'minute',
                        'method':'visible_management_and_public_fulltext',
                        'evidence_id':identifier,
                        'evidence_file':verification_path.relative_to(Path(database).resolve().parent/'publish-evidence').as_posix(),
                        'verification_sha256':_sha(verification_path.read_bytes())})
                proof['reconciled']=True
                _write(directory/'result.json',proof)
                return reconciled
            except Exception:
                if context:
                    await _save_failure_pages(context,directory)
                raise
            finally:
                # Cleanup does not undo a completed read-only reconciliation.
                # Always try both closures even if the first one fails.
                for name,closer in ([('context',context.close)] if context else []) + [('browser',browser.close)]:
                    try:
                        await closer()
                    except Exception as error:
                        logging.exception('Could not close result-check %s',name)
                        proof.setdefault('cleanup_warnings',[]).append({'resource':name,'error_type':type(error).__name__})
                        _write(directory/'result.json',proof)
    except Exception as error:
        proof.update(status='need_action',reconciled=False,
            reason=str(error) if isinstance(error,ValueError) else '窗口关闭、验证中断或页面异常，平台结果仍需核对',
            error_type=type(error).__name__)
        _write(directory/'result.json',proof)
        raise ResultCheckUnavailable(proof['reason']) from error


def result_check_running(database, job_id):
    with _lock:
        return (str(Path(database).resolve()),job_id) in _active


def start_result_check(database,cookies_directory,job_id):
    _load_binding(database,cookies_directory,job_id)
    key=(str(Path(database).resolve()),job_id)
    with _lock:
        if key in _active:
            raise ResultCheckUnavailable('这条任务正在只读核对，请在当前窗口正常完成登录或验证')
        _active.add(key)
    def run():
        try:
            asyncio.run(check_toutiao_result(database,cookies_directory,job_id,
                progress=lambda message:update_publish_job_progress(database,job_id,message=message)))
        except Exception as error:
            logging.exception('Read-only Toutiao result check did not reconcile job %s',job_id)
            try:
                update_publish_job_progress(database,job_id,message='只读核对未完成：'+str(error)+'；保留处理中，不会再次提交')
            except Exception:
                logging.exception('Could not persist result-check progress')
        finally:
            with _lock:
                _active.discard(key)
    thread=threading.Thread(target=run,daemon=True,name=f'toutiao-result-check-{job_id}')
    try:
        thread.start()
    except Exception:
        with _lock:
            _active.discard(key)
        raise
    return {'job_id':job_id,'status':'checking','message':'已打开只读核对流程；请在可见窗口正常完成登录或验证，不会再次提交'}
