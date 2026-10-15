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
    normalize_ui_sources, open_doubao_session, read_snapshot, write_evidence_result,
)
from services.doubao_source_cli import main


class SourceEvidenceTests(unittest.TestCase):
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

    async def test_dom_excludes_sidebar_authored_and_hidden_links(self):
        snapshot = await read_snapshot(self.page, self.config)
        self.assertTrue(snapshot["question_verified"])
        self.assertTrue(snapshot["completion_marker"])
        self.assertEqual(len(snapshot["sources"]), 1)
        self.assertEqual(snapshot["sources"][0]["href"], "https://source.example.org/article")
        self.assertIn("被引用的回答片段", snapshot["sources"][0]["linked_answer_text"])

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


if __name__ == "__main__":
    unittest.main()
