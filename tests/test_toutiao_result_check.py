"""Synthetic visible management/public pages; no platform login or publishing."""
import asyncio
import copy
import importlib.util
import json
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager,closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from db.createTable import initialize_database
from services.publish_job_service import (create_publish_job,claim_publish_job,get_publish_job,
    reconcile_publish_job_success,InvalidPublishJobTransitionError)
from services.toutiao_result_check import (HK,MANAGEMENT_URL,ResultCheckUnavailable,_load_binding,
    _time_for_card,check_toutiao_result,inspect_result_pages,start_result_check)


class BindingFixture:
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.database=self.root/'database.db'
        self.cookies=self.root/'cookies';self.cookies.mkdir()
        (self.cookies/'account.json').write_text('{"cookies":[],"origins":[]}',encoding='utf-8')
        initialize_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection,connection:
            project=connection.execute("INSERT INTO projects(name) VALUES('合成核对项目')").lastrowid
            self.article=connection.execute("INSERT INTO articles(project_id,title,content,tags,status) VALUES(?,?,?,?,?)",
                (project,'合成核对文章','第一段：精确的全文。\n\n第二段：不得仅匹配摘要。','[]','ready')).lastrowid
            self.account=connection.execute("INSERT INTO user_info(type,filePath,userName,status) VALUES(7,'account.json','合成账号',1)").lastrowid
        self.job=create_publish_job(self.database,article_id=self.article,platform='toutiao',account_id=self.account,demo=False)
        claim_publish_job(self.database,self.job['id'])
        submitted=datetime.now(timezone.utc)-timedelta(minutes=2)
        started=(submitted-timedelta(seconds=20)).replace(tzinfo=None).isoformat(sep=' ')
        with closing(sqlite3.connect(self.database)) as connection,connection:
            connection.execute('UPDATE publish_jobs SET started_at=? WHERE id=?',(started,self.job['id']))
        self.job=get_publish_job(self.database,self.job['id'])
        self.journal={'job_id':self.job['id'],'platform':'toutiao','submission_attempted':True,
            'updated_at':submitted.isoformat(),**{key:self.job[key] for key in ('article_id','account_id','started_at','authorization_fingerprint')}}
        self.journal_path=self.root/'publish-evidence'/f'job-{self.job["id"]}.json'
        self.journal_path.parent.mkdir()
        self.journal_path.write_text(json.dumps(self.journal),encoding='utf-8')
        self.binding=_load_binding(self.database,self.cookies,self.job['id'])


class ResultBindingTests(BindingFixture,unittest.TestCase):
    def test_readonly_api_has_desktop_gate_rejects_claimed_results_and_returns_background_status(self):
        from sau_backend import app
        keys=('DATABASE_PATH','COOKIES_DIRECTORY','DESKTOP_MODE','DEMO_MODE','TESTING')
        original={key:app.config.get(key) for key in keys}
        try:
            app.config.update(DATABASE_PATH=self.database,COOKIES_DIRECTORY=self.cookies,DESKTOP_MODE=False,DEMO_MODE=False,TESTING=True)
            client=app.test_client();url=f'/api/publish/jobs/{self.job["id"]}/check-result'
            self.assertEqual(client.post(url).status_code,403)
            app.config['DESKTOP_MODE']=True
            with patch('services.toutiao_result_check.start_result_check',return_value={'job_id':self.job['id'],'status':'checking','message':'只读核对'}) as launch:
                self.assertEqual(client.post(url,json={'url':'https://www.toutiao.com/article/999/','success':True}).status_code,400)
                launch.assert_not_called()
                response=client.post(url)
                self.assertEqual(response.status_code,202)
                self.assertEqual(response.json['data']['status'],'checking')
                launch.assert_called_once_with(self.database,self.cookies,self.job['id'])
            self.assertEqual(get_publish_job(self.database,self.job['id'])['status'],'processing')
        finally:app.config.update(original)

    def test_missing_attempt_changed_payload_account_and_cookie_escape_refuse_without_browser(self):
        for kind in ('no-attempt','wrong-job','changed-body','unavailable-account','cookie-escape'):
            with self.subTest(kind=kind):
                if kind in ('no-attempt','wrong-job'):
                    value={**self.journal,'submission_attempted':kind!='no-attempt','job_id':999 if kind=='wrong-job' else self.job['id']}
                    self.journal_path.write_text(json.dumps(value),encoding='utf-8')
                elif kind=='changed-body':
                    with closing(sqlite3.connect(self.database)) as c,c:
                        c.execute("UPDATE articles SET content='changed' WHERE id=?",(self.article,))
                elif kind=='unavailable-account':
                    with closing(sqlite3.connect(self.database)) as c,c:
                        c.execute('UPDATE user_info SET status=0 WHERE id=?',(self.account,))
                else:
                    with closing(sqlite3.connect(self.database)) as c,c:
                        c.execute("UPDATE user_info SET filePath='../escape.json' WHERE id=?",(self.account,))
                with self.assertRaises(ResultCheckUnavailable):
                    _load_binding(self.database,self.cookies,self.job['id'])
                self.journal_path.write_text(json.dumps(self.journal),encoding='utf-8')
                with closing(sqlite3.connect(self.database)) as c,c:
                    c.execute("UPDATE articles SET content=? WHERE id=?",(self.binding['article']['content'],self.article))
                    c.execute("UPDATE user_info SET status=1,filePath='account.json' WHERE id=?",(self.account,))
        self.assertEqual(get_publish_job(self.database,self.job['id'])['status'],'processing')

    def test_reconcile_atomically_rejects_changed_content_and_preserves_platform_time(self):
        expected={key:self.job[key] for key in ('article_id','account_id','started_at','authorization_fingerprint')}
        with closing(sqlite3.connect(self.database)) as c,c:
            c.execute("UPDATE articles SET content='edited while checking' WHERE id=?",(self.article,))
        with self.assertRaises(InvalidPublishJobTransitionError):
            reconcile_publish_job_success(self.database,self.job['id'],result_url='https://www.toutiao.com/article/123/',expected_binding=expected)
        with closing(sqlite3.connect(self.database)) as c,c:
            c.execute('UPDATE articles SET content=? WHERE id=?',(self.binding['article']['content'],self.article))
        platform_time=self.binding['submitted_at'].astimezone(HK).replace(second=0,microsecond=0).isoformat()
        result=reconcile_publish_job_success(self.database,self.job['id'],result_url='https://www.toutiao.com/article/123/',
            expected_binding=expected,platform_evidence={'published_at':platform_time,'precision':'minute','evidence_id':'synthetic-check'})
        self.assertEqual(result['platform_published_at'],platform_time)
        self.assertEqual(result['platform_published_precision'],'minute')
        self.assertTrue(result['result_checked_at'])
        self.assertNotEqual(result['finished_at'],platform_time)

    def test_card_year_and_minute_window_are_bound_to_attempt(self):
        submitted=datetime(2025,12,31,23,59,30,tzinfo=HK)
        self.assertEqual(_time_for_card('01-01 00:01',submitted).year,2026)
        self.assertIsNone(_time_for_card('12-30 23:59',submitted))
        self.assertIsNone(_time_for_card('01-01 01:00',submitted))
        self.assertIsNone(_time_for_card('not a date',submitted))

    def test_background_duplicates_do_not_start_a_second_browser(self):
        created=[]
        class DeferredThread:
            def __init__(self,**kwargs):created.append(kwargs)
            def start(self):pass
        from services import toutiao_result_check as service
        try:
            with patch.object(service.threading,'Thread',DeferredThread):
                first=start_result_check(self.database,self.cookies,self.job['id'])
                self.assertEqual(first['status'],'checking')
                with self.assertRaises(ResultCheckUnavailable):
                    start_result_check(self.database,self.cookies,self.job['id'])
            self.assertEqual(len(created),1)
        finally:
            with service._lock:service._active.clear()


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Playwright optional dependency missing')
class VisibleResultTests(BindingFixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright=await async_playwright().start()
        self.browser=await self.playwright.chromium.launch(headless=True)

    async def asyncTearDown(self):
        await self.browser.close();await self.playwright.stop()

    def pages(self,*,body=None,duplicate=False,old=False,wrong_destination=False,wrong_time=False,declaration=True):
        title=self.binding['title']
        stamp=self.binding['submitted_at'].astimezone(HK).replace(second=0,microsecond=0)
        card_stamp=stamp-timedelta(days=1) if old else stamp
        href='https://www.toutiao.com/item/123456/?enter_from=mp_group_management'
        onclick=' onclick="event.preventDefault();window.open(\'https://www.toutiao.com/article/999999/\')"' if wrong_destination else ''
        card=f'<div class="article-card"><div class="title-wrap"><a class="title" target="_blank" href="{href}"{onclick}>{title}</a><span class="label create-time">{card_stamp:%m-%d %H:%M}</span></div><div class="abstruct"><div class="byte-tag">已发布</div></div></div>'
        management=card+(card if duplicate else '')+'<button onclick="window.submissions++">发布</button>'
        paragraphs=''.join('<p>'+line+'</p>' for line in (body if body is not None else self.binding['body']).split('\n'))
        public_time=stamp+timedelta(minutes=1) if wrong_time else stamp
        ai='<div class="ai-meta">作品声明：内容由AI生成</div>' if declaration else ''
        public=f'<div class="article-content"><h1>{title}</h1><div class="article-meta"><span>{public_time:%Y-%m-%d %H:%M}</span></div>{ai}<article class="tt-article-content">{paragraphs}</article></div>'
        return '<meta charset="utf-8">'+management,'<meta charset="utf-8">'+public

    async def context_for(self,management,public):
        context=await self.browser.new_context()
        requests=[]
        async def route(value):
            requests.append((value.request.method,value.request.url))
            self.assertEqual(value.request.method,'GET','Read-only checker issued a mutation request')
            if value.request.url.startswith('https://mp.toutiao.com/'):
                await value.fulfill(content_type='text/html',body=management)
            elif value.request.url.startswith('https://www.toutiao.com/item/'):
                # Playwright routes only the first URL of an HTTP redirect;
                # a fulfilled 302 can let the next request escape this local
                # fixture. Exercise a same-article browser navigation instead,
                # so every GET, including the final public page, stays routed.
                await value.fulfill(content_type='text/html',body=
                    '<script>location.replace("https://www.toutiao.com/article/123456/")</script>')
            elif value.request.url.startswith('https://www.toutiao.com/article/'):
                await value.fulfill(content_type='text/html',body=public)
            else:
                await value.abort()
                self.fail('Unexpected external navigation')
        await context.route('**/*',route)
        return context,requests

    async def test_normal_management_link_public_fulltext_and_evidence_never_submit(self):
        context,requests=await self.context_for(*self.pages())
        try:
            page=await context.new_page();output=self.root/'inspect';output.mkdir()
            observed=await inspect_result_pages(page,context,self.binding,output,wait_for_user=AsyncMock(),timeout_seconds=3)
            self.assertEqual(observed['result_url'],'https://www.toutiao.com/article/123456/')
            self.assertEqual(observed['platform_article_id'],'123456')
            self.assertTrue((output/'management.json').is_file())
            self.assertTrue((output/'public.png').is_file())
            self.assertTrue(all(method=='GET' for method,_ in requests))
            self.assertIn(('GET','https://www.toutiao.com/article/123456/'),requests)
            self.assertEqual(get_publish_job(self.database,self.job['id'])['status'],'processing','Probe alone must not reconcile')
        finally:await context.close()

    async def test_complete_check_reconciles_with_immutable_hash_and_distinct_platform_time(self):
        management,public=self.pages()
        async def new_context(**kwargs):
            self.assertEqual(kwargs['storage_state'],str(self.cookies/'account.json'))
            context,_=await self.context_for(management,public)
            close=context.close
            async def interrupted_close():
                await close()
                raise RuntimeError('synthetic close failure after successful reconciliation')
            context.close=interrupted_close
            return context
        proxy=SimpleNamespace(new_context=new_context,is_connected=self.browser.is_connected,
            close=AsyncMock(side_effect=RuntimeError('synthetic browser cleanup failure')))
        launch=AsyncMock(return_value=proxy)
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        before=self.journal_path.read_bytes()
        with self.assertLogs(level='ERROR'):
            result=await check_toutiao_result(self.database,self.cookies,self.job['id'],playwright_factory=factory)
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['result_url'],'https://www.toutiao.com/article/123456/')
        evidence=json.loads(result['result_check_evidence'])
        verification=self.root/'publish-evidence'/evidence['evidence_file']
        self.assertEqual(hashlib.sha256(verification.read_bytes()).hexdigest(),evidence['verification_sha256'])
        state=json.loads((verification.parent/'result.json').read_text(encoding='utf-8'))
        self.assertTrue(state['reconciled'])
        self.assertEqual([warning['resource'] for warning in state['cleanup_warnings']],['context','browser'])
        proxy.close.assert_awaited_once()
        self.assertTrue(state['management_session_saved_before_public_navigation'])
        self.assertFalse(state['submission_attempted'])
        self.assertFalse(state['editor_write_attempted'])
        self.assertNotIn('reconciled',json.loads(verification.read_text(encoding='utf-8')))
        self.assertEqual(self.journal_path.read_bytes(),before,'Original submission evidence was overwritten')
        self.assertEqual(result['platform_published_precision'],'minute')
        self.assertNotEqual(result['platform_published_at'],result['result_checked_at'])

    async def test_actual_public_paragraph_shape_preserves_inline_url_without_fuzzy_text_matching(self):
        from services.toutiao_result_check import PUBLIC_JS
        page=await self.browser.new_page()
        try:
            html='<style>article span br:nth-of-type(2){display:none}</style><div class="article-content"><h1>合成标题</h1><div class="article-meta"><span>2026-10-20 04:09</span></div><div class="ai-meta">作品声明：内容由AI生成</div><article class="tt-article-content"><p>第一段。</p><p class="syl-page-br" style=""><br></p><p><span>公开说明（来源：<br>https://example.org/about/；<br>https://example.org/product/）。引号\\"原文\\"及空 格保留。</span></p></article></div>'
            await page.set_content(html)
            state=await page.evaluate(PUBLIC_JS)
            expected='第一段。\n\n公开说明（来源：https://example.org/about/；https://example.org/product/）。引号\\"原文\\"及空 格保留。'
            from services.toutiao_article_session import normalized_body
            self.assertTrue(state['ready'])
            self.assertNotEqual(normalized_body(state['body']),normalized_body(expected))
            self.assertEqual(normalized_body(state['paragraph_body']),normalized_body(expected))
            await page.locator('article span').evaluate('e=>e.style.display="none"')
            self.assertFalse((await page.evaluate(PUBLIC_JS))['ready'])
            await page.locator('article span').evaluate('e=>e.style.display="inline"')
            await page.locator('article').evaluate('e=>e.append(document.createElement("div"))')
            self.assertFalse((await page.evaluate(PUBLIC_JS))['ready'])
        finally:await page.close()

    async def test_same_title_duplicates_stale_time_wrong_popup_truncated_body_or_declaration_stay_unknown(self):
        for variant in ({'duplicate':True},{'old':True},{'wrong_destination':True},
                        {'body':'第一段：精确的全文。'},{'wrong_time':True},{'declaration':False}):
            with self.subTest(variant=variant):
                context,_=await self.context_for(*self.pages(**variant))
                try:
                    page=await context.new_page();output=self.root/('reject-'+str(len(list(self.root.iterdir()))));output.mkdir()
                    with self.assertRaises(ResultCheckUnavailable):
                        await inspect_result_pages(page,context,self.binding,output,wait_for_user=AsyncMock(),timeout_seconds=2)
                    self.assertEqual(get_publish_job(self.database,self.job['id'])['status'],'processing')
                finally:await context.close()
