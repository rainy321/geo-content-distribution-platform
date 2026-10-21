"""Observed editor shapes in isolated HTML; no account or platform submission."""
import hashlib
import html
import json
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
            +'''<button class="navigation_bar_more_actions_trigger" onclick="window.menuClicks++;const p=document.querySelector('.more-actions-popup');p.hidden=!p.hidden">更多操作</button>
            <div class="more-actions-popup" hidden><button class="more-actions-item" onclick="this.parentElement.hidden=true;document.getElementById('import-markdown-file-input').click()"> 导入 </button></div>
            <input id="import-markdown-file-input" type="file" accept=".md" hidden>
            <script>
            window.importCount=0;window.menuClicks=0;window.bodyInputEvents=0;
            window.originalDocument={id:'old-local-id',title:document.querySelector('input').value,text:document.querySelector('pre').textContent};
            document.querySelector('pre').addEventListener('input',()=>window.bodyInputEvents++);
            document.getElementById('import-markdown-file-input').addEventListener('change',e=>{
              window.importCount++;const file=e.currentTarget.files[0];window.importedName=file.name;window.importedMime=file.type;
              const reader=new FileReader();
              reader.onload=event=>{
                if(window.importMode==='read_failure')return;
                const source=event.target.result;window.decodedSource=source;window.markerAtImport=window.markerSaved===true;
                let text=(source+'\\n').replace(/\\n\\n$/,'\\n');
                if(window.importMode==='wrong_body')text=text.replace(/\\n/g,'');
                if(window.importMode==='extra_lf')text+='\\n';
                const section=document.createElement('div');section.className='cledit-section';section.textContent=text;
                document.querySelector('pre').replaceChildren(section);window.syntheticEditorModel=text;
                window.newDocument={id:'new-local-id',articleid:null,text};
                const title=document.querySelector('input');title.value=window.importMode==='wrong_title'?'其他文档':file.name.replace(/\\.[^.]+$/,'');
                title.style.display='none';document.querySelector('.article-bar__title-display').style.display='block';
                e.target.value='';
              };
              reader.readAsText(file);
            });
            </script></body></html>''')
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
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),ARTICLE['content']+'\n')
        self.assertEqual(await self.page.evaluate('window.syntheticEditorModel'),ARTICLE['content']+'\n')
        self.assertEqual(await self.page.evaluate('window.bodyInputEvents'),0)
        self.assertEqual(await self.page.evaluate('window.importCount'),1)
        self.assertRegex(await self.page.evaluate('window.importedName'),r'^geo-import-[a-f0-9]{32}\.md$')
        self.assertEqual(await self.page.evaluate('window.importedMime'),'text/markdown')
        self.assertEqual(await self.page.evaluate('window.originalDocument.text'),'')

    async def test_arbitrary_existing_draft_is_never_overwritten(self):
        await self.load('客户原有草稿，必须保留')
        before=Mock()
        with self.assertRaisesRegex(ValueError,'已有标题或草稿'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'客户原有草稿，必须保留')

    async def test_only_complete_known_welcome_can_be_replaced(self):
        welcome='合成完整欢迎教程\n请正常使用编辑器'
        media=[['IMG',f'https://example.invalid/welcome-{i}.png',None,None] for i in range(4)]
        images=''.join('<img src="'+entry[1]+'">' for entry in media)
        with patch.object(csdn_editor,'WELCOME_SHA256',hashlib.sha256(welcome.encode()).hexdigest()), patch.object(csdn_editor,'WELCOME_MEDIA_SHA256',hashlib.sha256(json.dumps(media,separators=(',',':')).encode()).hexdigest()):
            await self.load(html.escape(welcome)+images)
            state=await csdn_editor.probe_editor(self.page)
            self.assertEqual(state['body_state'],'welcome_example')
            self.assertIn('欢迎示例',state['message'])
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
            await self.load(html.escape(welcome+'客户新加内容'))
            with self.assertRaisesRegex(ValueError,'已有标题或草稿'):
                await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
            await self.load(html.escape(welcome))
            before=Mock()
            with self.assertRaisesRegex(ValueError,'媒体内容'):
                await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
            before.assert_not_called()

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
        before.assert_called_once()
        self.assertEqual(await self.page.locator('pre img').count(),1)
        self.assertEqual(await self.page.evaluate('window.importCount'),1)

    async def test_body_loaded_during_title_commit_is_preserved_after_marker(self):
        await self.load()
        await self.page.locator(csdn_editor.TITLE).evaluate("e=>e.addEventListener('keydown',event=>{if(event.key==='Enter')document.querySelector('pre').textContent='标题确认时加载的客户草稿'})")
        before=Mock()
        with self.assertRaisesRegex(ValueError,'全文回读不一致'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_called_once()
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'标题确认时加载的客户草稿')
        self.assertEqual(await self.page.evaluate('window.importCount'),1)

    async def test_normal_import_preserves_internal_lf_and_adds_only_known_final_lf(self):
        for suffix in ('','\n','\n\n'):
            await self.load()
            article={**ARTICLE,'content':'# 标题\r\n\r\n段落一\r\n\r\n段落二'+suffix}
            async def before():
                await self.page.evaluate('window.markerSaved=true')
            await csdn_editor.fill_editor(self.page,article,before_write=before)
            normalized=article['content'].replace('\r\n','\n')
            expected=normalized if normalized.endswith('\n') else normalized+'\n'
            self.assertEqual(await self.page.evaluate('window.decodedSource'),normalized)
            self.assertEqual(await self.page.evaluate('window.syntheticEditorModel'),expected)
            self.assertTrue(await self.page.evaluate('window.markerAtImport'))
            self.assertFalse((await self.page.evaluate('window.decodedSource')).startswith('\ufeff'))

    async def test_open_menu_is_reused_without_toggling_it_closed(self):
        await self.load()
        await self.page.locator(csdn_editor.MORE_POPUP).evaluate('e=>e.hidden=false')
        await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
        self.assertEqual(await self.page.evaluate('window.menuClicks'),0)
        self.assertEqual(await self.page.evaluate('window.importCount'),1)

    async def test_failed_read_wrong_document_or_body_never_succeeds_or_reimports(self):
        for mode in ('read_failure','wrong_title','wrong_body','extra_lf'):
            await self.load()
            await self.page.evaluate('mode=>window.importMode=mode',mode)
            before=Mock()
            with patch.object(csdn_editor,'READBACK_TIMEOUT_MS',350),self.assertRaisesRegex(ValueError,'新文档标题与完整正文'):
                await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
            before.assert_called_once()
            self.assertEqual(await self.page.evaluate('window.importCount'),1)
            self.assertEqual(await self.page.evaluate('window.bodyInputEvents'),0)
            self.assertNotEqual(await self.page.locator(csdn_editor.TITLE).input_value(),ARTICLE['title'])

    async def test_marker_time_source_race_stops_before_file_selection(self):
        await self.load()
        async def before():
            await self.page.locator(csdn_editor.BODY).evaluate("e=>e.textContent='持久化时出现的客户草稿'")
        with self.assertRaisesRegex(ValueError,'编辑内容在检查后发生变化'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        self.assertEqual(await self.page.evaluate('window.importCount'),0)
        self.assertEqual(await self.page.locator(csdn_editor.BODY).text_content(),'持久化时出现的客户草稿')

    async def test_media_added_by_menu_click_stops_before_marker(self):
        await self.load()
        await self.page.locator(csdn_editor.MORE).evaluate("e=>e.addEventListener('click',()=>document.querySelector('pre').appendChild(document.createElement('img')))")
        before=Mock()
        with self.assertRaisesRegex(ValueError,'媒体内容'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.evaluate('window.importCount'),0)

    async def test_unique_import_identity_does_not_depend_on_stale_article_id_url(self):
        await self.load('明确恢复的合成partial稿',title=ARTICLE['title'])
        await self.page.evaluate("history.replaceState({},'',location.pathname+'?articleId=123')")
        source={'url':self.page.url,'title':ARTICLE['title'],'text':'明确恢复的合成partial稿'}
        with self.assertRaisesRegex(ValueError,'已有标题或草稿'):
            await csdn_editor.fill_editor(self.page,ARTICLE,before_write=Mock())
        before=Mock()
        result=await csdn_editor._import_document(self.page,ARTICLE,before_write=before,expected_source=source)
        self.assertEqual(result['state'],'filled')
        self.assertTrue(self.page.url.endswith('?articleId=123'))
        self.assertEqual(await self.page.evaluate('window.newDocument.articleid'),None)
        self.assertEqual(await self.page.evaluate('window.originalDocument.text'),source['text'])
        self.assertEqual(await self.page.evaluate('window.importCount'),1)
        before.assert_called_once()

    async def test_unconfirmed_source_and_restore_metadata_are_rejected_before_import(self):
        await self.load()
        before=Mock()
        with self.assertRaisesRegex(ValueError,'检查点'):
            await csdn_editor._import_document(self.page,ARTICLE,before_write=before,expected_source={})
        for content in ('\ufeff正文','正文\n<!--stackedit_data:abc-->'):
            with self.assertRaisesRegex(ValueError,'无 BOM'):
                await csdn_editor.fill_editor(self.page,{**ARTICLE,'content':content},before_write=before)
        before.assert_not_called()
        self.assertEqual(await self.page.evaluate('window.importCount'),0)

    async def test_zero_quota_and_changed_title_are_not_fill_ready(self):
        await self.load(extra='<div class="publish-quota-tip">今日发文额度还有 0 篇</div>')
        self.assertFalse((await csdn_editor.probe_editor(self.page))['editor_ready'])
        await self.load(title='客户已有标题')
        self.assertFalse((await csdn_editor.probe_editor(self.page))['editor_ready'])


if __name__=='__main__':
    unittest.main()
