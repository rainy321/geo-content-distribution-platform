"""Observed editor shapes in isolated HTML; no account or platform submission."""
import hashlib
import html
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from playwright.async_api import async_playwright
from services import csdn_editor

URL = 'https://editor.csdn.net/md/'
ARTICLE = {'title':'已有模型 API，售后助手还要核对什么', 'content':'# 合成工程检查\n\n这里是经过审稿的合成正文。\n\n仅测试普通编辑器输入，不发布。'}


class CsdnRecipeScopeTests(unittest.TestCase):
    def test_current_page_allows_only_the_observed_autosave_parameter(self):
        self.assertTrue(csdn_editor._current_page_supported(URL+'?articleId=123'))
        for suffix in ('?articleId=0','?articleId=-1','?articleId=abc','?articleId=1&articleId=2','?other=1','?articleId=12#x'):
            with self.subTest(suffix=suffix):
                self.assertFalse(csdn_editor._current_page_supported(URL+suffix))

    def test_only_observed_editor_origin_and_entry_are_supported(self):
        self.assertTrue(csdn_editor.supports({'editor_url': URL}))
        self.assertTrue(csdn_editor.supports({'editor_url': 'https://editor.csdn.net:443/md'}))
        for url in ('http://editor.csdn.net/md/', 'https://editor.csdn.net.evil.example/md/',
                    'https://editor.csdn.net:9000/md/', 'https://u:p@editor.csdn.net/md/',
                    'https://editor.csdn.net/md/?articleId=1', 'https://editor.csdn.net/md/#article',
                    'https://editor.csdn.net/md/123', 'https://mp.csdn.net/', None):
            with self.subTest(url=url):
                self.assertFalse(csdn_editor.supports({'editor_url':url}))


class CsdnEditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p = await async_playwright().start()
        self.addAsyncCleanup(self.p.stop)
        paths=[Path(self.p.chromium.executable_path)]
        if os.name=='nt':
            paths += [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
        executable=next((p for p in paths if p.is_file()),None)
        if not executable:
            self.skipTest('Local Chromium or Edge required for isolated editor fixture')
        self.browser=await self.p.chromium.launch(executable_path=str(executable),headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context=await self.browser.new_context()
        self.addAsyncCleanup(self.context.close)
        self.page=await self.context.new_page()
        self.document=''
        async def route(r):
            if r.request.url==URL:
                await r.fulfill(status=200,content_type='text/html; charset=utf-8',body=self.document)
            else:
                await r.abort()
        await self.context.route('**/*',route)

    async def load(self, body='', title='【无标题】', extra=''):
        self.document=('''<html><body><div class="article-bar__title-display" onclick="this.style.display='none';document.querySelector('input').style.display='block'">标题</div>'''
            +'<input type="text" maxlength="100" class="article-bar__title article-bar__title--input text-input" style="display:none" value="'+html.escape(title,quote=True)+'">'
            +'<pre contenteditable="true" style="min-height:100px" class="editor__inner markdown-highlighting"><div class="cledit-section">'+body+'</div></pre>'+extra
            +'''<script>let batch=[],timer;document.querySelector('pre').addEventListener('input',e=>{const editor=e.currentTarget;batch.push(e.data===null?'\\n':e.data);clearTimeout(timer);timer=setTimeout(()=>{const text=batch.join('');batch=[];const section=document.createElement('div');section.className='cledit-section';section.textContent=text;editor.replaceChildren(section);window.syntheticEditorModel=text;},0);});</script></body></html>''')
        await self.page.goto(URL,wait_until='load')

    async def test_hidden_title_activation_and_full_plaintext_readback(self):
        await self.load()
        before=Mock()
        state=await csdn_editor.probe_editor(self.page)
        self.assertEqual(state['body_state'],'empty')
        self.assertIsNone(state['login_required'])
        self.assertTrue(state['editor_ready'])
        result=await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_called_once()
        self.assertEqual(result['state'],'filled')
        self.assertEqual(await self.page.locator(csdn_editor.TITLE).input_value(),ARTICLE['title'])
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),ARTICLE['content'])
        self.assertEqual(await self.page.evaluate('window.syntheticEditorModel'),ARTICLE['content'])

    async def test_arbitrary_existing_draft_is_never_overwritten(self):
        await self.load('客户原有草稿，必须保留')
        before=Mock()
        with self.assertRaisesRegex(ValueError,'已有标题或草稿'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'客户原有草稿，必须保留')

    async def test_only_complete_known_welcome_can_be_replaced(self):
        welcome='合成完整欢迎教程\n请正常使用编辑器'
        with patch.object(csdn_editor,'WELCOME_SHA256',hashlib.sha256(welcome.encode()).hexdigest()):
            await self.load(html.escape(welcome))
            state=await csdn_editor.probe_editor(self.page)
            self.assertEqual(state['body_state'],'welcome_example')
            self.assertIn('欢迎示例',state['message'])
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
            await self.load(html.escape(welcome+'客户新加内容'))
            with self.assertRaisesRegex(ValueError,'已有标题或草稿'):
                await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())

    async def test_failed_durable_marker_prevents_any_content_write(self):
        await self.load()
        with self.assertRaisesRegex(RuntimeError,'storage unavailable'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock(side_effect=RuntimeError('storage unavailable')))
        self.assertEqual(await self.page.locator(csdn_editor.TITLE).input_value(),'【无标题】')
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'')

    async def test_only_normal_template_cancel_is_used(self):
        modal='''<div class="modal"><div class="normal-cancel-btn" onclick="this.parentElement.style.display='none'">取消</div><div>添加到正文</div></div>'''
        await self.load(extra=modal)
        await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
        self.assertFalse(await self.page.locator('.modal').is_visible())

    async def test_verification_and_media_stop_before_writes(self):
        await self.load(extra='<div>请完成安全验证</div>')
        before=Mock()
        with self.assertRaises(ValueError):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        for media in ('<img alt="已有客户素材">','<audio></audio>','<svg></svg>','<canvas></canvas>'):
            await self.load(media)
            with self.assertRaisesRegex(ValueError,'媒体内容'):
                await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()

    async def test_draft_loaded_after_initial_probe_is_preserved(self):
        await self.load()
        original=csdn_editor._fields
        calls=0
        async def delayed_fields(page):
            nonlocal calls
            calls+=1
            if calls==2:
                await page.locator(csdn_editor.BODY).evaluate("e=>e.textContent='异步加载的客户草稿'")
            return await original(page)
        before=Mock()
        with patch.object(csdn_editor,'_fields',delayed_fields),self.assertRaisesRegex(ValueError,'已有草稿'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'异步加载的客户草稿')

    async def test_media_added_during_title_activation_is_preserved(self):
        await self.load()
        await self.page.locator(csdn_editor.TITLE_DISPLAY).evaluate("e=>e.addEventListener('click',()=>document.querySelector('pre').appendChild(document.createElement('img')))")
        before=Mock()
        with self.assertRaisesRegex(ValueError,'媒体内容'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.locator('pre img').count(),1)

    async def test_body_loaded_during_title_commit_is_preserved_after_marker(self):
        await self.load()
        await self.page.locator(csdn_editor.TITLE).evaluate("e=>e.addEventListener('keydown',event=>{if(event.key==='Enter')document.querySelector('pre').textContent='标题确认时加载的客户草稿'})")
        before=Mock()
        with self.assertRaisesRegex(ValueError,'标题输入后'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_called_once()
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'标题确认时加载的客户草稿')

    async def test_zero_quota_and_changed_title_are_not_fill_ready(self):
        await self.load(extra='<div class="publish-quota-tip">今日发文额度还有 0 篇</div>')
        self.assertFalse((await csdn_editor.probe_editor(self.page))['editor_ready'])
        await self.load(title='客户已有标题')
        self.assertFalse((await csdn_editor.probe_editor(self.page))['editor_ready'])


if __name__=='__main__':
    unittest.main()
