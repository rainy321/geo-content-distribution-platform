import asyncio
import importlib.util
import json
import tempfile
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.doubao_source_collector import (
    DOUBAO_URL, CollectorConfig, CompletionTracker, _blocked_reason, _browser_error_reason, _expand_sources,
    _navigate_to_doubao, collect_doubao_sources,
    normalize_ui_sources, open_doubao_session, read_snapshot, write_evidence_result, _citation_title_match,
)
from services.doubao_source_cli import main
from services.doubao_submission_transport import EDITOR_READBACK_JS, LITERAL_PREAMBLE, submission_fence, submission_text


class SourceEvidenceTests(unittest.TestCase):
    def test_citation_author_suffix_needs_exact_unique_headline_and_public_author(self):
        title = '公开核验标题_合成发布者'
        document = {'url': 'https://www.toutiao.com/article/123456/', 'title': '公开核验标题 - 今日头条',
                    'headlines': [{'text': '公开核验标题'}], 'authors': [{'text': '合成发布者'}]}
        self.assertEqual(_citation_title_match(title, document), 'visible_h1_and_public_author_exact')
        for change in ({'authors': []}, {'authors': [{'text': '另一发布者'}]},
                       {'headlines': [{'text': '另一标题'}]},
                       {'headlines': [{'text': '公开核验标题'}, {'text': '其他文章标题'}]},
                       {'url': 'https://www.toutiao.com.evil.example/article/123456/'},
                       {'url': 'https://www.toutiao.com/search/123456/'}):
            with self.subTest(change=change):
                self.assertIsNone(_citation_title_match(title, {**document, **change}))
        self.assertEqual(_citation_title_match('公开核验标题', document), 'visible_h1_exact')
        self.assertEqual(_citation_title_match('公开核验标题', {'title': '公开核验标题'}), 'document_title_exact')
        self.assertIsNone(_citation_title_match('公开核验标题', {'title': '另一标题'}))

    def test_original_exact_document_title_still_accepts_different_hero_headings(self):
        for title, headings in (
            ('Developer Platform | OpenRouter', ['Build with every model. From one platform.']),
            ('SiliconFlow – AI Infrastructure for LLMs & Multimodal Models', ['One Platform All Your AI Inference Needs']),
            ('LiteLLM — Open-Source AI Gateway & LLM Proxy', ['The AI Gateway for platform teams']),
            ('Official Docs | Provider', ['Quickstart', 'API Reference']),
        ):
            with self.subTest(title=title):
                self.assertEqual(_citation_title_match(title, {'title': title,
                    'headlines': [{'text': h} for h in headings]}), 'document_title_exact')

    def test_only_ui_scopes_are_sources_and_missing_links_stay_missing(self):
        result = normalize_ui_sources([
            {"scope": "navigation", "title": "导航", "href": "https://other.example/"},
            {"scope": "answer_link", "title": "模型列出的链接", "href": "https://invented.example/"},
            {"scope": "source_panel", "title": "只有标题", "href": None},
            {"scope": "source_panel", "title": "不可打开", "href": "javascript:alert(1)"},
            {"scope": "source_panel", "title": "错误链接", "href": "https://[bad"},
            {"scope": "source_panel", "title": "含凭证", "href": "https://secret:password@example.org/"},
        ])
        self.assertEqual(len(result), 4)
        self.assertTrue(all(item["url"] is None for item in result))
        self.assertEqual(result[0]["url_status"], "not_exposed")

    def test_repeated_citations_keep_all_fragments_and_observed_title(self):
        result = normalize_ui_sources([
            {"scope": "inline_citation", "title": "[1]", "href": "https://site.example/a", "linked_answer_text": "片段甲"},
            {"scope": "inline_citation", "title": "[1]", "href": "https://site.example/a", "linked_answer_text": "片段乙"},
            {"scope": "source_panel", "title": "页面来源标题", "href": "https://site.example/a"},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "页面来源标题")
        self.assertEqual(result[0]["domain"], "site.example")
        self.assertEqual(result[0]["linked_answer_fragments"], ["片段甲", "片段乙"])
        self.assertEqual(result[0]["evidence_type"], "ui_citation")

    def test_redirect_is_preserved_without_guessing_target_or_availability(self):
        url = "https://www.doubao.com/redirect?target=opaque-value"
        item = normalize_ui_sources([{"scope": "source_panel", "title": "来源", "href": url}])[0]
        self.assertEqual(item["url"], url)
        self.assertEqual(item["domain"], "www.doubao.com")
        self.assertEqual(item["url_status"], "displayed_not_opened")

    def test_evidence_manifest_detects_changed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "answer.txt").write_text("原始回答", encoding="utf-8")
            result = {"citations": []}
            path = write_evidence_result(root, result)
            saved = json.loads(path.read_text(encoding="utf-8"))
            digest = saved["evidence_manifest"][0]["sha256"]
            (root / "answer.txt").write_text("更改后的回答", encoding="utf-8")
            write_evidence_result(root, result)
            self.assertNotEqual(digest, result["evidence_manifest"][0]["sha256"])
            self.assertEqual(len(result["evidence_manifest"]), 1)

    def test_blocking_states_are_reported_before_completion(self):
        self.assertEqual(_blocked_reason({"challenge": True, "login_required": True}), "human_verification_required")
        self.assertEqual(_blocked_reason({"login_required": True}), "login_required")
        self.assertEqual(_blocked_reason({"rate_limited": True}), "rate_limited")
        self.assertEqual(_blocked_reason({"session_reset": True}), "session_redirected_to_logout")


class CompletionTests(unittest.TestCase):
    def snapshot(self, **changes):
        return {"answer": "测试回答", "assistant_count": 1, "question_verified": True,
                "completion_marker": True, "busy": False, **changes}

    def test_quiet_stream_without_end_marker_is_not_complete(self):
        tracker = CompletionTracker(0)
        tracker.observe(self.snapshot(completion_marker=False), 1)
        self.assertFalse(tracker.observe(self.snapshot(completion_marker=False), 100))

    def test_capacity_error_copy_marker_is_not_a_valid_completion(self):
        tracker = CompletionTracker(0)
        tracker.observe(self.snapshot(service_unavailable=True), 1)
        self.assertFalse(tracker.observe(self.snapshot(service_unavailable=True), 100))
        self.assertEqual(_blocked_reason({"service_unavailable": True}), "service_capacity_unavailable")

    def test_requires_fresh_turn_and_verified_submission(self):
        tracker = CompletionTracker(1)
        tracker.observe(self.snapshot(), 1)
        self.assertFalse(tracker.observe(self.snapshot(), 100))
        self.assertFalse(tracker.observe(self.snapshot(assistant_count=2, question_verified=False), 101))

    def test_stream_updates_reset_stability_and_busy_blocks_completion(self):
        tracker = CompletionTracker(0, stable_seconds=3)
        tracker.observe(self.snapshot(), 1)
        self.assertFalse(tracker.observe(self.snapshot(answer="新内容"), 5))
        self.assertFalse(tracker.observe(self.snapshot(answer="新内容", busy=True), 10))
        self.assertTrue(tracker.observe(self.snapshot(answer="新内容"), 11))

    def test_cli_rejects_bad_timeout_before_launching_browser(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main(["collect", "--timeout", "-1"])
        self.assertEqual(error.exception.code, 2)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_aborted_navigation_accepted_only_with_fixed_url_and_visible_ui(self):
        wait = AsyncMock()
        page = SimpleNamespace(url=DOUBAO_URL, goto=AsyncMock(side_effect=RuntimeError('net::ERR_ABORTED')),
                               locator=Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=wait))),
                               is_closed=Mock(return_value=False))
        await _navigate_to_doubao(page, 10)
        self.assertEqual(page.goto.await_count, 1)
        wait.assert_awaited_once()
        page.url = 'https://other.example/'
        with self.assertRaises(RuntimeError):
            await _navigate_to_doubao(page, 10)

    async def test_only_transient_initial_load_is_retried(self):
        page = SimpleNamespace(goto=AsyncMock(side_effect=[RuntimeError('net::ERR_CONNECTION_CLOSED'), None]), is_closed=Mock(return_value=False))
        with patch('services.doubao_source_collector.asyncio.sleep', new_callable=AsyncMock):
            await _navigate_to_doubao(page, 1)
        self.assertEqual(page.goto.await_count, 2)
        page.goto.reset_mock(side_effect=True)
        page.goto.side_effect = RuntimeError('net::ERR_ABORTED')
        with self.assertRaises(RuntimeError):
            await _navigate_to_doubao(page, 1)
        self.assertEqual(page.goto.await_count, 1)
        self.assertEqual(_browser_error_reason(RuntimeError('net::ERR_CONNECTION_CLOSED secret-path')), 'network_error:ERR_CONNECTION_CLOSED')


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright optional browser dependency missing")
class BrowserExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.config = CollectorConfig(question="测试问题")
        fixture = Path(__file__).parent / "fixtures" / "doubao" / "collector-ui.html"
        self.fixture_html = fixture.read_text(encoding="utf-8")
        await self.page.set_content(self.fixture_html)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_source_progress_counts_visible_citations_with_existing_cap_and_keeps_evidence(self):
        markers=''.join('<a data-citation href="https://source.example/known">已知来源</a>' if index==0 else
                        f'<span class="container-sWvQla" style="{"display:none" if index==1 else ""}">引用标记{index}</span>' for index in range(33))
        await self.page.set_content(f'<div data-message-role="assistant"><div data-testid="message-content">回答{markers}</div></div>')
        phases=[]
        with tempfile.TemporaryDirectory() as directory, patch('services.doubao_source_collector._resolve_citation_navigation', new_callable=AsyncMock, return_value=(None,None)) as resolve:
            observed=await _expand_sources(self.page,self.config,Path(directory),progress=phases.append)
            self.assertEqual(resolve.await_count,28)
            self.assertTrue(observed[2])
            citation=[item for item in phases if item['phase']=='inline_citations']
            self.assertEqual([(item['processed'],item['total']) for item in citation],[(count,29) for count in range(30)])
            self.assertTrue(all(set(item)=={'event','phase','processed','total'} for item in phases))
            def broken_observer(_event):
                raise RuntimeError('synthetic observer failure')
            unchanged=await _expand_sources(self.page,self.config,Path(directory),progress=broken_observer)
            self.assertEqual(observed,unchanged)
            self.assertEqual(resolve.await_count,56)

    async def test_source_progress_reports_already_open_panel_without_clicking_it_again(self):
        await self.page.set_content('''<div data-message-role="assistant"><div data-testid="message-content">回答</div>
          <div data-testid="search-references"><button data-testid="search-references-button" onclick="window.replayed=true">查看来源</button>
          <a data-testid="reference-item" href="https://source.example/reference"><h3>已展开来源</h3></a></div></div>''')
        phases=[]
        with tempfile.TemporaryDirectory() as directory:
            await _expand_sources(self.page,self.config,Path(directory),progress=phases.append)
        self.assertEqual([(item['processed'],item['total']) for item in phases if item['phase']=='source_panels'],[(0,1),(1,1)])
        self.assertFalse(await self.page.evaluate('Boolean(window.replayed)'))

    async def test_dom_excludes_sidebar_authored_and_hidden_links(self):
        snapshot = await read_snapshot(self.page, self.config)
        self.assertTrue(snapshot["question_verified"])
        self.assertTrue(snapshot["completion_marker"])
        self.assertEqual(len(snapshot["sources"]), 1)
        self.assertEqual(snapshot["sources"][0]["href"], "https://source.example.org/article")
        self.assertIn("被引用的回答片段", snapshot["sources"][0]["linked_answer_text"])

    async def test_literal_code_dom_does_not_normalize_quotes_backslashes_punctuation_or_extra_content(self):
        question = '{"quoted":"\\\"same\\\"","url":"https://example.org/a?x=1"}'
        config = CollectorConfig(question=question,submission_format='verbatim_fenced')
        html = '<div data-message-role="user"><div data-testid="message_text_content"><p></p><pre><code></code></pre></div><div data-testid="message_expand_all_button">展开全部</div></div>'
        await self.page.set_content(html)
        await self.page.locator('p').evaluate('(e,text)=>e.textContent=text',LITERAL_PREAMBLE)
        for content,valid in ((question,True),(question+'\n',True),(question+'\n\n',False),
                              (question.replace('\\"','"'),False),(question.replace(':','：'),False)):
            await self.page.locator('code').evaluate('(e,text)=>e.textContent=text',content)
            snap = await read_snapshot(self.page,config)
            self.assertFalse(snap['question_verified'])
            self.assertEqual(snap['verbatim_question_verified'],valid)
        await self.page.locator('code').evaluate('(e,text)=>e.textContent=text',question)
        await self.page.locator('p').evaluate('e=>e.textContent += "额外指令"')
        self.assertFalse((await read_snapshot(self.page,config))['verbatim_question_verified'])

    async def test_observed_literal_fence_presentation_requires_complete_exact_envelope(self):
        question = '{"quoted":"\\\"literal\\\""}\n```json\n{}\n```'
        config = CollectorConfig(question=question, submission_format='verbatim_fenced')
        await self.page.set_content('<div data-message-role="user"><div data-testid="message_text_content"><p></p><pre><code></code></pre></div></div>')
        await self.page.locator('p').evaluate('(e,text)=>e.textContent=text', LITERAL_PREAMBLE)
        fence = submission_fence(question)
        wrapped = fence+'text\n'+question+'\n'+fence
        boundary = fence+'text\n'+question+'\n\n'+fence
        variants = ((question,'literal_code_payload'), (question+'\n','literal_code_payload_trailing_newline'),
            (wrapped,'literal_code_contains_outer_fence'),
            (boundary,'literal_code_contains_outer_fence_extra_boundary_newline'))
        for content, variant in variants:
            await self.page.locator('code').evaluate('(e,text)=>e.textContent=text', content)
            state = await read_snapshot(self.page, config)
            self.assertTrue(state['verbatim_question_verified'],variant)
            self.assertEqual(state['question_presentation_variant'],variant)
        for altered in (boundary+'\n', boundary.replace('\n\n'+fence, '\n\n\n'+fence),
                        boundary.replace('\\"','"'), boundary.replace('text\n','json\n',1),
                        boundary.replace(':','：'), boundary.replace(fence,'```'),
                        'extra\n'+boundary, boundary.replace('literal','changed')):
            await self.page.locator('code').evaluate('(e,text)=>e.textContent=text', altered)
            state = await read_snapshot(self.page, config)
            self.assertFalse(state['verbatim_question_verified'])
            self.assertIsNone(state['question_presentation_variant'])
        await self.page.locator('code').evaluate('(e,text)=>e.textContent=text', boundary)
        await self.page.locator('pre').evaluate('e=>e.after(e.cloneNode(true))')
        self.assertFalse((await read_snapshot(self.page,config))['verbatim_question_verified'])

    async def test_observed_prosemirror_plain_paragraphs_preserve_bytes_and_reject_unknown_layouts(self):
        expected = 'first\n\n{"quoted":"\\\"literal\\\"","path":"C:\\\\data"}\nlast'
        await self.page.set_content('<div class="tiptap ProseMirror" contenteditable="true"></div>')
        editor = self.page.locator('[contenteditable]')
        await editor.evaluate('''(e, lines) => e.replaceChildren(...lines.map(line => {
            const p=document.createElement('p'); if(line) p.textContent=line;
            else p.append(document.createElement('br')); return p;
        }))''', expected.split('\n'))
        proof = await editor.evaluate(EDITOR_READBACK_JS, expected)
        self.assertNotEqual(proof['inner_text'], expected)
        self.assertEqual(proof['method'],'prosemirror_direct_plain_paragraphs')
        self.assertEqual(proof['readback'],expected)
        original = await editor.inner_html()
        for mutation in (
            'e.firstChild.innerHTML="<span>first</span>"',
            'e.firstChild.setAttribute("hidden", "")',
            'e.appendChild(document.createElement("div"))',
            'e.childNodes[1].appendChild(document.createTextNode("extra"))',
            'e.childNodes[1].firstChild.setAttribute("style","display:none")',
            'e.firstChild.textContent="first\\nextra"',
        ):
            await editor.evaluate('(e,html)=>e.innerHTML=html',original)
            await editor.evaluate('e=>{'+mutation+'}')
            rejected = await editor.evaluate(EDITOR_READBACK_JS, expected)
            self.assertIsNone(rejected['readback'],mutation)
        await editor.evaluate('(e,html)=>e.innerHTML=html',original)
        await editor.evaluate('e=>e.children[2].textContent=e.children[2].textContent.replace("literal","changed")')
        altered = await editor.evaluate(EDITOR_READBACK_JS, expected)
        self.assertNotEqual(altered['readback'],expected)
        await self.page.set_content('<textarea></textarea>')
        textarea=self.page.locator('textarea')
        await textarea.fill(expected)
        exact = await textarea.evaluate(EDITOR_READBACK_JS, expected)
        self.assertEqual(exact['readback'],expected)
        self.assertEqual(exact['method'],'form_value')

    async def test_visible_guest_login_button_blocks_before_submission(self):
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend', '<button data-testid=\"to_login_button\">登录</button>')")
        snapshot = await read_snapshot(self.page, self.config)
        self.assertEqual(snapshot["login_state"], "guest")
        self.assertEqual(_blocked_reason(snapshot), "login_required")

    async def test_workspace_mode_is_recorded_separately(self):
        snapshot = await read_snapshot(self.page, self.config)
        self.assertEqual(snapshot["client_mode"], "consumer")
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend', '<button data-testid=\"workspace-switch-chip\">豆包 工作</button>')")
        snapshot = await read_snapshot(self.page, self.config)
        self.assertEqual(snapshot["client_mode"], "work")

    async def test_actual_capacity_error_text_is_a_blocking_service_message(self):
        await self.page.locator('[data-testid="message_text_content"]').last.evaluate("e => e.innerText = '算力紧张，办公能力得晚点才能用了。开通订阅，享高峰期优先使用。'")
        snapshot = await read_snapshot(self.page, self.config)
        self.assertTrue(snapshot['service_unavailable'])
        self.assertEqual(_blocked_reason(snapshot), 'service_capacity_unavailable')

    async def test_work_citation_hover_title_opens_only_matching_source(self):
        await self.page.set_content('''<div data-message-role="user">测试问题</div>
        <div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">示例来源</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">示例来源</span>
        <div class="title-ehsWfR" onclick="window.open('https://work-source.example/reference?id=42','_blank')">具体来源标题</div></div>''')
        await self.page.context.route('https://work-source.example/**', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<title>具体来源标题</title>Synthetic source'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            sources = normalize_ui_sources(items)
            self.assertFalse(incomplete)
            self.assertEqual(sources[0]['url'], 'https://work-source.example/reference?id=42')
            self.assertEqual(sources[0]['title'], '具体来源标题')
            self.assertEqual(sources[0]['title_kind'], 'ui_title')

    async def test_hover_card_switch_during_pointer_travel_does_not_attribute_url(self):
        await self.page.set_content('''<div data-message-role="user">测试问题</div>
        <div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">来源甲</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">来源甲</span>
        <div class="title-ehsWfR" onmouseenter="this.innerText='另一来源标题';this.previousElementSibling.innerText='来源乙'"
             onclick="window.open('https://wrong-source.example/reference','_blank')">来源甲标题</div></div>''')
        await self.page.context.route('https://wrong-source.example/**', lambda route: route.fulfill(status=200, body='Synthetic source'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            sources = normalize_ui_sources(items)
            self.assertFalse(incomplete)
            self.assertIsNone(sources[0]['url'])
            self.assertEqual(sources[0]['title'], '来源甲')
            evidence = json.loads((Path(directory) / 'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['resolution_status'], 'citation_card_changed_during_click')
            self.assertEqual(evidence['clicked_publisher'], '来源乙')

    async def test_noopener_context_page_waits_for_public_headline_and_author(self):
        await self.page.set_content('''<div data-message-role="user">测试问题</div>
        <div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">今日头条</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">今日头条</span>
        <div class="title-ehsWfR" onclick="window.sourceClicks=(window.sourceClicks||0)+1;
            window.open('https://www.toutiao.com/article/123456/','_blank','noopener')">公开核验标题_合成发布者</div></div>''')
        async def delayed(route):
            await asyncio.sleep(1.2)
            await route.fulfill(status=200, content_type='text/html; charset=utf-8', body='''
                <title>公开核验标题 - 今日头条</title><h1>公开核验标题</h1>
                <span class="name"><a href="/author">合成发布者</a></span>
                <a class="user-name" href="/author">合成发布者</a>''')
        await self.page.context.route('https://www.toutiao.com/**', delayed)
        # No popup API association is needed; noopener pages belong to context.
        with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'expect_popup',
                side_effect=AssertionError('must use context page events')) as popup_api:
            items, _, _ = await _expand_sources(self.page, CollectorConfig(client_mode='work'), Path(directory))
            sources = normalize_ui_sources(items)
            self.assertEqual(sources[0]['url'], 'https://www.toutiao.com/article/123456/')
            self.assertEqual(sources[0]['title'], '公开核验标题_合成发布者')
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 1)
            popup_api.assert_not_called()
            evidence = json.loads((Path(directory)/'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['new_page_count'], 1)
            self.assertEqual(evidence['destination_match_method'], 'visible_h1_and_public_author_exact')
            self.assertEqual(evidence['destination_title'], '公开核验标题 - 今日头条')
            self.assertEqual(evidence['destination_headlines'][0]['text'], '公开核验标题')
            self.assertEqual(evidence['destination_authors'][0]['text'], '合成发布者')

    async def test_exact_official_document_title_with_different_hero_h1_is_attributed(self):
        await self.page.set_content('''<div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">OpenRouter</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">OpenRouter</span>
        <div class="title-ehsWfR" onclick="window.open('https://official-source.example/developers','_blank','noopener')">Developer Platform | OpenRouter</div></div>''')
        await self.page.context.route('https://official-source.example/**', lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8',
            body='<title>Developer Platform | OpenRouter</title><h1>Build with every model. From one platform.</h1>'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, _ = await _expand_sources(self.page, CollectorConfig(client_mode='work'), Path(directory))
            self.assertEqual(normalize_ui_sources(items)[0]['url'], 'https://official-source.example/developers')
            evidence = json.loads((Path(directory)/'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['destination_match_method'], 'document_title_exact')
            self.assertEqual(evidence['destination_headlines'][0]['text'], 'Build with every model. From one platform.')

    async def test_multiple_context_pages_from_one_click_are_not_attributed(self):
        await self.page.set_content('''<div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">来源甲</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">来源甲</span>
        <div class="title-ehsWfR" onclick="window.open('https://multi-source.example/one','_blank','noopener');
            window.open('https://multi-source.example/two','_blank','noopener')">明确标题</div></div>''')
        await self.page.context.route('https://multi-source.example/**', lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8', body='<title>明确标题</title><h1>明确标题</h1>'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, _ = await _expand_sources(self.page, CollectorConfig(client_mode='work'), Path(directory))
            self.assertIsNone(normalize_ui_sources(items)[0]['url'])
            evidence = json.loads((Path(directory)/'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['new_page_count'], 2)
            self.assertEqual(evidence['resolution_status'], 'multiple_new_pages_not_attributed')

    async def test_delayed_hover_card_waits_for_this_publisher_before_one_click(self):
        await self.page.set_content('''<div data-message-role="user">测试问题</div>
        <div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="setTimeout(() => {
            document.querySelector('.footer-website-VEYotM').innerText='当前来源';
            document.querySelector('.title-ehsWfR').innerText='当前来源的核验标题';
            window.cardReady=true;
        }, 850)">当前来源</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show">
        <span class="footer-website-VEYotM">上一来源</span>
        <div class="title-ehsWfR" onclick="window.sourceClicks=(window.sourceClicks||0)+1;
            window.clickedReady=!!window.cardReady;
            window.open(window.cardReady?'https://delayed-source.example/article':'https://wrong-source.example/old','_blank')">旧标题</div></div>''')
        await self.page.context.route('https://delayed-source.example/**', lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8', body='<title>当前来源的核验标题</title>'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, _ = await _expand_sources(self.page, self.config, Path(directory))
            sources = normalize_ui_sources(items)
            self.assertEqual(sources[0]['url'], 'https://delayed-source.example/article')
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 1)
            self.assertTrue(await self.page.evaluate('window.clickedReady'))
            evidence = json.loads((Path(directory)/'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['hover_card_wait_status'], 'matched')
            self.assertEqual(evidence['clicked_publisher'], '当前来源')
            self.assertEqual(evidence['destination_title'], '当前来源的核验标题')

    async def test_popup_destination_with_different_title_is_not_attributed(self):
        await self.page.set_content('''<div data-message-role="user">测试问题</div>
        <div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onmouseenter="document.querySelector('[role=tooltip]').style.display='block'">来源甲</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show" style="display:none">
        <span class="footer-website-VEYotM">来源甲</span>
        <div class="title-ehsWfR" onclick="window.open('https://wrong-source.example/reference','_blank')">当前来源标题</div></div>''')
        await self.page.context.route('https://wrong-source.example/**', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body='<title>上一来源标题</title>'))
        with tempfile.TemporaryDirectory() as directory:
            items, _, _ = await _expand_sources(self.page, self.config, Path(directory))
            sources = normalize_ui_sources(items)
            self.assertIsNone(sources[0]['url'])
            self.assertEqual(sources[0]['title'], '当前来源标题')
            evidence = json.loads((Path(directory) / 'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['resolution_status'], 'destination_title_not_confirmed')
            self.assertEqual(evidence['destination_title'], '上一来源标题')
            self.assertEqual(evidence['unconfirmed_navigation_url'], 'https://wrong-source.example/reference')

    async def test_work_hover_publisher_timeout_does_not_click_or_reuse_stale_card(self):
        await self.page.set_content('''<div data-message-role="assistant"><div data-testid="message-content">回答
        <span class="container-sWvQla" onclick="window.sourceClicks=(window.sourceClicks||0)+1">当前来源</span>
        </div><button data-testid="message_action_copy">复制</button></div>
        <div role="tooltip" class="semi-popover-wrapper-show">
        <span class="footer-website-VEYotM">上一来源</span>
        <div class="title-ehsWfR" onclick="window.sourceClicks=(window.sourceClicks||0)+1">旧标题</div></div>''')
        with tempfile.TemporaryDirectory() as directory:
            items, _, _ = await _expand_sources(self.page, CollectorConfig(client_mode='work'), Path(directory))
            self.assertEqual(await self.page.evaluate('window.sourceClicks || 0'), 0)
            self.assertIsNone(normalize_ui_sources(items)[0]['url'])
            evidence = json.loads((Path(directory)/'citation-1.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['hover_card_wait_status'], 'publisher_match_timeout')
            self.assertEqual(evidence['resolution_status'], 'work_citation_card_not_matched')
            self.assertEqual(evidence['navigation_urls'], [])

    async def test_expanding_visible_source_control_preserves_title_only_card(self):
        with tempfile.TemporaryDirectory() as directory:
            items, screenshots, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            sources = normalize_ui_sources(items)
            self.assertFalse(incomplete)
            self.assertEqual(len(sources), 2)
            self.assertEqual(sources[0]["title"], "真实 DOM 中的测试来源标题")
            self.assertIsNone(sources[1]["url"])
            self.assertTrue((Path(directory) / screenshots[0]).is_file())

    async def test_unrecognized_source_panel_reports_incomplete(self):
        await self.page.locator('[data-testid="search-references"]').evaluate("e => e.removeAttribute('data-testid')")
        with tempfile.TemporaryDirectory() as directory:
            _items, _screenshots, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            self.assertTrue(incomplete)

    async def test_old_external_panel_is_not_attributed_to_current_answer(self):
        await self.page.locator('[data-testid="search-references"]').evaluate("e => e.hidden = false")
        snapshot = await read_snapshot(self.page, self.config)
        self.assertEqual(len(snapshot["sources"]), 1)
        self.assertEqual(snapshot["sources"][0]["scope"], "inline_citation")

    async def test_current_layout_separates_materials_from_clicked_inline_citation(self):
        fixture = Path(__file__).parent / 'fixtures' / 'doubao' / 'collector-current-layout.html'
        await self.page.set_content(fixture.read_text(encoding='utf-8'))
        await self.page.context.route('https://source.example/**', lambda route: route.fulfill(status=200, body='Synthetic source target'))
        snapshot = await read_snapshot(self.page, self.config)
        self.assertIn('第一段回答。\n\n第二段引用文字', snapshot['answer'])
        self.assertEqual(snapshot['source_ui_summaries'], ['搜索 2 个关键词，参考 2 篇资料'])
        with tempfile.TemporaryDirectory() as directory:
            items, screenshots, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            citations = normalize_ui_sources([x for x in items if x['scope'] == 'inline_citation'])
            materials = normalize_ui_sources([x for x in items if x['scope'] == 'source_panel'])
            self.assertFalse(incomplete)
            self.assertEqual(len(materials), 2)
            self.assertEqual(materials[0]['title'], '测试文章标题')
            self.assertEqual(len(citations), 1)
            self.assertEqual(citations[0]['url'], 'https://source.example/article?f_link_type=inline')
            self.assertIsNone(citations[0]['ui_href'])
            self.assertEqual(citations[0]['url_observation_method'], 'ui_click_navigation')
            self.assertIn('第二段引用文字', citations[0]['linked_answer_text'])
            self.assertTrue((Path(directory) / 'citation-1.json').is_file())
            self.assertEqual(len(screenshots), 2)

    async def test_open_materials_and_mixed_citations_keep_the_correct_marker_target(self):
        fixture = Path(__file__).parent / 'fixtures' / 'doubao' / 'collector-current-layout.html'
        await self.page.set_content(fixture.read_text(encoding='utf-8'))
        await self.page.context.route('https://source.example/**', lambda route: route.fulfill(status=200, body='Synthetic source target'))
        await self.page.locator('#materials').evaluate('e => e.hidden = false')
        await self.page.locator('[data-testid="message_text_content"]').nth(1).evaluate('''e => e.insertAdjacentHTML('beforeend', '<a data-citation href="https://source.example/first">[1]</a>')''')
        with tempfile.TemporaryDirectory() as directory:
            items, _screenshots, incomplete = await _expand_sources(self.page, self.config, Path(directory))
            citations = normalize_ui_sources([x for x in items if x['scope'] == 'inline_citation'])
            self.assertFalse(incomplete)
            self.assertTrue(await self.page.locator('#materials').is_visible())
            self.assertEqual(len(citations), 2)
            self.assertEqual(citations[0]['url'], 'https://source.example/first')
            self.assertEqual(citations[1]['url'], 'https://source.example/article?f_link_type=inline')

    def local_browser_factory(self, html, contexts=None):
        async def create_context(*_args, **_kwargs):
            context = await self.browser.new_context(viewport={"width": 1200, "height": 800})
            # A route-fulfilled local fixture is never a real Doubao response.
            await context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
            if contexts is not None:
                contexts.append(context)
            return context

        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=create_context))

        return factory

    async def test_half_rendered_user_then_exact_text_completes_without_resending(self):
        html = self.fixture_html.replace('<div data-message-role="assistant">', '<div data-message-role="assistant" hidden>')
        html += '''<script>
            document.querySelector('[data-message-role=user]').textContent='';
            window.submissions=0;
            document.querySelector('[contenteditable]').addEventListener('keydown', e => {
                if (e.key==='Enter') {
                    e.preventDefault(); window.submissions++;
                    const exact=e.target.innerText;
                    document.querySelector('[data-message-role=user]').textContent='尚未渲染完整的文字';
                    setTimeout(() => document.querySelector('[data-message-role=user]').textContent=exact, 800);
                    setTimeout(() => document.querySelector('[data-message-role=assistant]').hidden=false, 1200);
                }
            });
        </script>'''
        observations = []
        async def observed(page, config, **kwargs):
            value = await read_snapshot(page, config, **kwargs)
            observations.append((value['user_message_count'], value['question_verified']))
            return value
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = CollectorConfig(question='测试问题', output_root=root/'evidence', profile_dir=root/'profile',
                                     stable_seconds=.1, timeout_seconds=5)
            with patch('services.doubao_source_collector.read_snapshot', observed):
                result, _ = await collect_doubao_sources(config, playwright_factory=self.local_browser_factory(html))
            self.assertIn((1, False), observations)
            self.assertIn((1, True), observations)
            self.assertTrue(result['answer_completed'], result)
            self.assertTrue(result['question_submitted'])
            self.assertIsNone(result['submission_receipt']['invalid_reason'])
            self.assertEqual(result['submission_receipt']['submission_attempts'], 1)

    async def test_internal_fenced_prompt_has_editor_and_dom_proof_with_one_enter(self):
        question = '任务：只输出 JSON。\n资料：{"quoted":"\\\"literal\\\"","path":"C:\\\\data"}\n```json\n{}\n```'
        html = self.fixture_html.replace('<div data-message-role="assistant">','<div data-message-role="assistant" hidden>')
        # Tiptap preserves authored newlines; the minimal unstyled fixture's
        # default white-space:normal would instead collapse its innerText.
        html = html.replace('contenteditable="true"', 'contenteditable="true" style="white-space:pre-wrap"')
        html = html.replace('class="tiptap"', 'class="tiptap ProseMirror"')
        html += '<button data-testid="chat_input_action_model" onclick="window.modelClicks++">豆包2.1 Lite<br>高</button>'
        html += '''<script>
            const user=document.querySelector('[data-message-role=user]'); user.hidden=true; user.textContent='';
            window.submissions=0; window.modelClicks=0;
            document.querySelector('[contenteditable]').addEventListener('input',e=>{
                if(e.target.childNodes.length && [...e.target.childNodes].every(n=>n.nodeName==='P')) return;
                const lines=e.target.innerText.split('\\n');
                e.target.replaceChildren(...lines.map(line=>{
                    const p=document.createElement('p'); p.textContent=line; return p;
                }));
            });
            document.querySelector('[contenteditable]').addEventListener('keydown',e=>{
                if(e.key==='Enter'){
                    e.preventDefault(); window.submissions++;
                    const sent=[...e.target.children].map(p=>p.textContent).join('\\n'), lines=sent.split('\\n');
                    const body=document.createElement('div'); body.dataset.testid='message_text_content';
                    const intro=document.createElement('p'); intro.textContent=lines[0];
                    const pre=document.createElement('pre'),code=document.createElement('code');
                    code.textContent=lines.slice(2,-1).join('\\n')+'\\n'; pre.append(code); body.append(intro,pre);
                    user.replaceChildren(body); user.hidden=false;
                    document.querySelector('[data-message-role=assistant]').hidden=false;
                }
            });
        </script>'''
        contexts, sent = [], asyncio.Event()
        def progress(event):
            if event['event']=='question_sent':
                sent.set()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=CollectorConfig(question=question,submission_format='verbatim_fenced',
                requested_model_label='豆包2.1 Lite',
                output_root=root/'evidence',profile_dir=root/'profile',stable_seconds=.1,timeout_seconds=5)
            task=asyncio.create_task(collect_doubao_sources(config,playwright_factory=self.local_browser_factory(html,contexts),progress=progress))
            try:
                await asyncio.wait_for(sent.wait(),timeout=8)
            except asyncio.TimeoutError:
                if task.done():
                    result, failed_path = await task
                    readback_path = failed_path.parent/'editor-readback.json'
                    self.fail(str(result)+' editor='+readback_path.read_text(encoding='utf-8'))
                raise
            count=await contexts[0].pages[0].evaluate('window.submissions')
            model_clicks=await contexts[0].pages[0].evaluate('window.modelClicks')
            result,path=await task
            self.assertEqual(count,1)
            self.assertEqual(model_clicks,0)
            self.assertEqual(result['environment']['requested_model_label'],'豆包2.1 Lite')
            self.assertEqual(result['environment']['model_label'],'豆包2.1 Lite\n高')
            self.assertTrue(result['answer_completed'],result)
            self.assertTrue(result['question_submitted'])
            self.assertEqual(result['question'],question)
            self.assertEqual(result['question_verification_method'],'verbatim_fenced_dom_with_editor_readback')
            self.assertEqual(result['question_presentation_variant'],'literal_code_payload_trailing_newline')
            observed=json.loads((path.parent/'submission-observed.json').read_text(encoding='utf-8'))
            self.assertFalse(observed['question_verified'])
            self.assertTrue(observed['verbatim_question_verified'])
            editor=json.loads((path.parent/'editor-readback.json').read_text(encoding='utf-8'))
            self.assertEqual(editor['editor_readback'],submission_text(question,'verbatim_fenced'))
            self.assertEqual(editor['editor_readback_method'],'prosemirror_direct_plain_paragraphs')
            self.assertNotEqual(editor['editor_inner_text'],editor['editor_readback'])
            self.assertTrue(editor['verified'])

    async def test_full_local_pipeline_writes_answer_sources_and_evidence(self):
        html = self.fixture_html.replace('<div data-message-role="assistant">', '<div data-message-role="assistant" hidden>')
        html += '''<script>
            document.querySelector('[data-message-role=user]').textContent = '';
            document.querySelector('[contenteditable]').addEventListener('keydown', e => {
                if(e.key === 'Enter') {
                    e.preventDefault();
                    document.querySelector('[data-message-role=user]').textContent = e.target.innerText;
                    document.querySelector('[data-message-role=assistant]').hidden = false;
                    e.target.innerText = '';
                }
            });
        </script>'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = CollectorConfig(question="测试问题", output_root=root / "evidence", profile_dir=root / "profile", stable_seconds=0.1, timeout_seconds=5)
            result, path = await collect_doubao_sources(config, playwright_factory=self.local_browser_factory(html))
            self.assertEqual(result["collection_status"], "success", result)
            self.assertTrue(result["question_submitted"])
            self.assertTrue(result["answer_completed"])
            self.assertEqual(result["visible_citation_count"], 1)
            self.assertEqual(result["visible_source_count"], 2)
            self.assertEqual(result["ui_citation_count"], 1)
            self.assertIsNone(result["retrieval_results"])
            self.assertTrue((path.parent / "submission-observed.json").is_file())
            self.assertGreaterEqual(len(result["evidence_manifest"]), 9)

    async def test_challenge_saves_evidence_without_submitting_question(self):
        html = self.fixture_html.replace('<div data-message-role="assistant">', '<div data-message-role="assistant" hidden>')
        html += '<div id="captcha-test-only">测试验证弹窗</div>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = CollectorConfig(question="测试问题", output_root=root / "evidence", profile_dir=root / "profile")
            result, path = await collect_doubao_sources(config, playwright_factory=self.local_browser_factory(html))
            self.assertEqual(result["collection_status"], "need_action")
            self.assertEqual(result["failure_reason"], "human_verification_required")
            self.assertFalse(result["question_submission_attempted"])
            self.assertFalse(result["answer_completed"])
            self.assertIsNone(result["visible_citation_count"])
            self.assertTrue((path.parent / "before.png").is_file())

    async def test_login_window_survives_cancelled_startup_navigation(self):
        created = []

        async def create_context(*_args, **_kwargs):
            context = await self.browser.new_context()
            await context.route("**/*", lambda route: route.abort("aborted"))
            created.append(context)
            return context

        @asynccontextmanager
        async def factory():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=create_context))

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()) as output:
            config = CollectorConfig(profile_dir=Path(directory) / "profile")
            # A cancelled navigation previously aborted the whole manual-login
            # flow; now the context survives until the user wait completes.
            await open_doubao_session(config, wait_seconds=0.1, playwright_factory=factory)
            self.assertIn("浏览器保持打开", output.getvalue())
            self.assertEqual(len(created), 1)

    async def test_manual_verification_continues_same_question_without_resubmission(self):
        html = self.fixture_html.replace('<div data-message-role="assistant">', '<div data-message-role="assistant" hidden>')
        html += '''<script>
            window.submissions = 0;
            document.querySelector('[data-message-role=user]').textContent = '';
            document.querySelector('[contenteditable]').addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault(); window.submissions += 1;
                    document.querySelector('[data-message-role=user]').textContent = e.target.innerText;
                    const challenge = document.createElement('div'); challenge.id = 'captcha-test-only';
                    challenge.innerHTML = '<button id="manual-test-confirm">模拟用户完成验证</button>';
                    document.body.append(challenge);
                    challenge.firstChild.onclick = () => {
                        challenge.remove(); document.querySelector('[data-message-role=assistant]').hidden = false;
                    };
                }
            });
        </script>'''
        contexts = []
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory)
            config = CollectorConfig(question="测试问题", output_root=root / 'evidence', profile_dir=root / 'profile',
                                     headed=True, manual_wait_seconds=5, stable_seconds=0.1, timeout_seconds=5)
            task = asyncio.create_task(collect_doubao_sources(config, playwright_factory=self.local_browser_factory(html, contexts)))
            try:
                for _ in range(100):
                    if contexts and contexts[0].pages:
                        page = contexts[0].pages[0]
                        try:
                            if await page.locator('#manual-test-confirm').count():
                                break
                        except Exception as exc:
                            if 'Execution context was destroyed' not in str(exc):
                                raise
                    await asyncio.sleep(0.05)
                else:
                    self.fail('Synthetic manual verification window did not appear')
                # This test click simulates the user, outside the collector.
                await page.locator('#manual-test-confirm').click()
                submissions = await page.evaluate('window.submissions')
                result, path = await task
                self.assertEqual(submissions, 1)
                self.assertEqual(result['collection_status'], 'success')
                self.assertTrue(result['question_submitted'])
                self.assertTrue(result['manual_verification']['events'][0]['cleared'])
                self.assertTrue((path.parent / 'manual-action-1.png').is_file())
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_local_to_canonical_after_manual_keeps_receipt_when_user_bubble_disappears(self):
        html = self.fixture_html.replace('<div data-message-role="assistant">', '<div data-message-role="assistant" hidden>')
        html += '''<script>
            window.submissions = 0;
            document.querySelector('[data-message-role=user]').textContent = '';
            document.querySelector('[contenteditable]').addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault(); window.submissions += 1;
                    history.replaceState({}, '', '/chat/local_12345');
                    document.querySelector('[data-message-role=user]').textContent = e.target.innerText;
                    const challenge = document.createElement('div'); challenge.id = 'captcha-test-only';
                    challenge.innerHTML = '<button id="manual-transition-confirm">模拟正常验证结束</button>';
                    document.body.append(challenge);
                    challenge.firstChild.onclick = () => {
                        challenge.remove();
                        document.querySelector('[data-message-role=user]').remove();
                        history.pushState({}, '', '/chat/987654');
                        document.querySelector('[data-message-role=assistant]').hidden = false;
                    };
                }
            });
        </script>'''
        contexts, phases = [], []
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory)
            config = CollectorConfig(question='测试问题', output_root=root/'evidence', profile_dir=root/'profile',
                                     headed=True, manual_wait_seconds=None, stable_seconds=.1, timeout_seconds=5)
            task = asyncio.create_task(collect_doubao_sources(config, playwright_factory=self.local_browser_factory(html, contexts), progress=phases.append))
            try:
                for _ in range(100):
                    if any(event['event']=='manual_action_required' for event in phases):
                        break
                    await asyncio.sleep(.05)
                else:
                    self.fail('The synthetic collector did not record its manual phase')
                page = contexts[0].pages[0]
                # A local fixture click simulates the account owner; it is not
                # a real service challenge or a collector verification solver.
                await page.locator('#manual-transition-confirm').click()
                submissions = await page.evaluate('window.submissions')
                result, path = await task
                self.assertTrue(result['answer_completed'], result)
                self.assertEqual(submissions, 1)
                self.assertEqual(result['question_verification_method'], 'bound_local_to_canonical_after_manual')
                receipt = json.loads((path.parent/'submission-observed.json').read_text(encoding='utf-8'))
                self.assertTrue(receipt['question_verified'])
                final = json.loads((path.parent/'answer.json').read_text(encoding='utf-8'))
                self.assertFalse(final['question_verified'])
                self.assertEqual(final['user_message_count'], 0)
                self.assertEqual(result['submission_receipt']['navigation_urls'], [
                    'https://www.doubao.com/chat/local_12345', 'https://www.doubao.com/chat/987654'])
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
