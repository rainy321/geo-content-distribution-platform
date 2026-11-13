"""Offline public citation labels; all navigation is fulfilled by local routes.

The five marker/card pairs reproduce the observed truncated publisher labels.
URLs below are deliberately synthetic, never reconstructed missing source URLs.
"""
import html
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from services import doubao_source_collector as collector


CCTV = ('央视网新闻...', '央视网新闻频道', '“AI中转站”，风险要防范_新闻频道_央视网(cctv.com)')
CHINA = ('中国网新闻...', '中国网新闻中心', '“AI中转站”风险不容小觑 国家安全部发布提示_中国网')
OBSERVED = (CCTV, CHINA, CCTV, CCTV, CHINA)
CHAT = 'https://www.doubao.com/chat/123456789'
QUESTION = '合成来源采集问题'
SOURCE = 'https://source.example/synthetic-citation'


class PublisherMatchTests(unittest.TestCase):
    def test_five_observed_labels_require_explicit_long_ellipsis_and_guard(self):
        for marker, publisher, _ in OBSERVED:
            self.assertEqual(collector._publisher_label_match(marker, publisher, allow_truncated=True),
                             'truncated_prefix_guarded')
            self.assertIsNone(collector._publisher_label_match(marker, publisher))
        self.assertEqual(collector._publisher_label_match('央视网新闻…', CCTV[1], allow_truncated=True),
                         'truncated_prefix_guarded')
        self.assertEqual(collector._publisher_label_match('央视网 新闻频道', '央视网新闻频道'), 'exact')

    def test_short_inexact_foreign_or_unmarked_prefixes_never_match(self):
        for marker, publisher in (('央视...', CCTV[1]), ('央视…', CCTV[1]),
                ('央视网新闻', CCTV[1]), ('央视网新闻..', CCTV[1]),
                ('央视网新闻...文字', CCTV[1]), ('中国网经济...', CHINA[1]),
                ('央视网新闻...', CHINA[1]), ('.....', CCTV[1]), ('', '')):
            self.assertIsNone(collector._publisher_label_match(marker, publisher, allow_truncated=True))


class CitationPublisherBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.requests = []
        self.page_html = ''
        self.title = CCTV[2]

        async def route(request):
            self.requests.append((request.request.method, request.request.url))
            body = self.page_html if request.request.url == CHAT else (
                '<!doctype html><meta charset="utf-8"><title>' + html.escape(self.title)
                + '</title><h1>' + html.escape(self.title) + '</h1>')
            await request.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)

        await self.context.route('**/*', route)

    async def load(self, case=CCTV, *, panel=None, duplicate_card=False):
        marker, publisher, self.title = case
        tooltip = '''<div role="tooltip" class="semi-popover-wrapper-show" id="card" style="display:none">
          <span class="footer-website-VEYotM">PUBLISHER</span>
          <div class="title-ehsWfR" onclick="window.sourceClicks++;window.open('SOURCE','_blank')">TITLE</div>
        </div>'''.replace('PUBLISHER', html.escape(publisher)).replace('TITLE', html.escape(self.title)).replace('SOURCE', SOURCE)
        marker_html = '<span class="container-sWvQla" onmouseenter="document.getElementById(\'card\').style.display=\'block\'">' + html.escape(marker) + '</span>'
        if panel is True:
            marker_html, tooltip = '', ''
        panel_html = ''
        if panel is not None:
            cards = ('<a data-testid="reference-item" href="https://materials.example/one">'
                     '<h3>合成资料标题</h3></a>') if panel else '<p>暂无可见材料明细</p>'
            panel_html = '''<button data-testid="search-references-button" onclick="document.getElementById('materials').style.display='block'">查看来源</button>
              <div data-testid="search-references" id="materials" style="display:none">''' + cards + '</div>'
        self.page_html = '''<!doctype html><meta charset="utf-8">
          <button data-testid="workspace-switch-chip">豆包对话</button>
          <button data-testid="chat_input_action_model">快速</button><textarea></textarea>
          <div data-message-role="user">QUESTION</div>
          <div data-message-role="assistant"><div data-testid="message-content">合成完整回答。MARKER</div>
          <button data-testid="message_action_copy">复制</button>PANEL</div>
          TOOLTIP<script>window.sourceClicks=0;</script>'''.replace('QUESTION', QUESTION).replace('MARKER', marker_html).replace('PANEL', panel_html).replace('TOOLTIP', tooltip)
        await self.page.goto(CHAT)
        if duplicate_card:
            await self.page.locator('#card').evaluate("e=>{e.style.display='block';const copy=e.cloneNode(true);copy.id='other-card';e.after(copy)}")

    async def expand(self, path, **options):
        config = collector.CollectorConfig(question=QUESTION, client_mode='consumer', strict_client_mode=True)
        snapshot = await collector.read_snapshot(self.page, config)
        (path/'answer.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf8')
        return await collector._expand_sources(self.page, config, path, initial_evidence_file='answer.json', **options)

    async def test_five_real_label_pairs_keep_dom_evidence_when_only_image_times_out(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        for case in OBSERVED:
            with self.subTest(marker=case[0]):
                await self.load(case)
                with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot',
                        AsyncMock(side_effect=ImageTimeout('synthetic image timeout'))):
                    root = Path(directory)
                    items, screenshots, incomplete = await self.expand(root)
                    sources = collector.normalize_ui_sources(items)
                    self.assertFalse(incomplete)
                    self.assertEqual(sources[0]['url'], SOURCE)
                    self.assertEqual(sources[0]['title'], case[2])
                    self.assertEqual(sources[0]['evidence_file'], 'citation-1.json')
                    self.assertEqual(screenshots, [])
                    self.assertFalse((root/'citation-1.png').exists())
                    evidence = json.loads((root/'citation-1.json').read_bytes())
                    self.assertEqual(evidence['screenshot_status'], 'image_timeout')
                    self.assertTrue(evidence['guarded_DOM_verified_after_image_timeout'])
                    self.assertEqual(evidence['publisher_match_method'], 'truncated_prefix_guarded')
                    self.assertEqual(evidence['bound_publisher'], case[1])
                    self.assertEqual(evidence['clicked_publisher'], case[1])
                    self.assertEqual(evidence['hover_card_wait_status'], 'matched')
                    self.assertEqual(evidence['observed_url'], SOURCE)
                    self.assertEqual(json.loads((root/'source-context.json').read_bytes())['blocked'], False)
                    self.assertEqual(await self.page.evaluate('window.sourceClicks'), 1)

    async def test_two_visible_cards_or_foreign_publisher_cannot_authorize_timeout_fallback_click(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        for duplicate in (True, False):
            await self.load(duplicate_card=duplicate)
            if not duplicate:
                await self.page.locator('.footer-website-VEYotM').evaluate("e=>e.textContent='中国网新闻中心'")
            with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot',
                    AsyncMock(side_effect=ImageTimeout('synthetic image timeout'))):
                root = Path(directory)
                items, _, _ = await self.expand(root)
                self.assertIsNone(collector.normalize_ui_sources(items)[0]['url'])
                self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
                self.assertEqual(json.loads((root/'citation-1.json').read_bytes())['failure_stage'], 'citation_screenshot')

    async def test_timeout_with_conversation_change_stops_without_source_click_or_foreign_dom(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        await self.load()
        async def change_then_timeout(**_kwargs):
            await self.page.evaluate("history.pushState({},'', '/chat/999999999')")
            raise ImageTimeout('synthetic image timeout')
        with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot', change_then_timeout):
            root = Path(directory)
            items, screenshots, incomplete = await self.expand(root)
            self.assertTrue(incomplete)
            self.assertEqual(screenshots, [])
            self.assertIsNone(collector.normalize_ui_sources(items)[0]['url'])
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
            self.assertFalse((root/'citation-1.json').exists())
            self.assertTrue(json.loads((root/'source-context.json').read_bytes())['blocked'])

    async def test_non_timeout_image_error_does_not_click_or_claim_a_fallback(self):
        await self.load()
        with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot',
                AsyncMock(side_effect=RuntimeError('synthetic non-timeout failure'))):
            root = Path(directory)
            items, _, _ = await self.expand(root)
            self.assertIsNone(collector.normalize_ui_sources(items)[0]['url'])
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
            evidence = json.loads((root/'citation-1.json').read_bytes())
            self.assertNotIn('guarded_DOM_verified_after_image_timeout', evidence)
            self.assertEqual(evidence['error_type'], 'RuntimeError')

    async def test_bound_card_change_during_image_wait_is_not_clicked(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        await self.load()
        async def replace_title_then_timeout(**_kwargs):
            await self.page.locator('.title-ehsWfR').evaluate("e=>e.textContent='另一条合成标题'")
            raise ImageTimeout('synthetic image timeout')
        with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot', replace_title_then_timeout):
            root = Path(directory)
            items, _, _ = await self.expand(root)
            self.assertIsNone(collector.normalize_ui_sources(items)[0]['url'])
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 0)
            self.assertEqual(json.loads((root/'citation-1.json').read_bytes())['resolution_status'],
                             'citation_card_changed_before_click')

    async def test_panel_timeout_keeps_verified_dom_sources_but_inline_cannot_fill_missing_materials(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        for material_present in (True, False):
            await self.load(panel=material_present)
            with tempfile.TemporaryDirectory() as directory, patch.object(self.page, 'screenshot',
                    AsyncMock(side_effect=ImageTimeout('synthetic image timeout'))):
                root = Path(directory)
                items, screenshots, incomplete = await self.expand(root)
                materials = [item for item in items if item['scope'] == 'source_panel']
                self.assertEqual(bool(materials), material_present)
                self.assertEqual(incomplete, not material_present)
                self.assertEqual(screenshots, [])
                evidence = json.loads((root/'sources-1.json').read_bytes())
                self.assertEqual(evidence['source_capture']['screenshot_status'], 'image_timeout')
                if material_present:
                    self.assertEqual(materials[0]['evidence_file'], 'sources-1.json')
                    self.assertEqual(materials[0]['href'], 'https://materials.example/one')

    async def test_existing_answer_reinspection_keeps_original_partial_and_manifest_valid_json_sidecar(self):
        from playwright.async_api import TimeoutError as ImageTimeout
        from services import geo_probe_source_inspection as inspection
        await self.load()
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True)
        snapshot = await collector.read_snapshot(self.page, config)
        real_screenshot = self.page.screenshot
        async def only_source_images_timeout(**options):
            if options.get('timeout') == 5000:
                raise ImageTimeout('synthetic source image timeout')
            return await real_screenshot(**options)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root/'original/result.json'
            original.parent.mkdir()
            snapshot_raw = json.dumps(snapshot, ensure_ascii=False).encode('utf8')
            (original.parent/'answer.json').write_bytes(snapshot_raw)
            payload = {'schema_version':'1.1','run_id':'synthetic-original',
                'question':QUESTION,'response':snapshot['answer'],'question_submitted':True,
                'question_submission_attempted':True,'answer_completed':True,
                'timestamp':'2026-11-12T00:00:00+08:00','conversation_url':CHAT,
                'collection_status':'partial','source_extraction_status':'incomplete',
                'failure_reason':'source_ui_not_resolved','citations':[],'displayed_sources':[],
                'environment':{'client_mode':'consumer','model_label':'快速','session_slot':'primary'},
                'evidence_manifest':[{'path':'answer.json','bytes':len(snapshot_raw),
                    'sha256':hashlib.sha256(snapshot_raw).hexdigest()}]}
            original_raw = json.dumps(payload, ensure_ascii=False).encode('utf8')
            original.write_bytes(original_raw)
            inspect_config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True,
                output_root=root/'inspections')
            with patch.object(self.page, 'screenshot', only_source_images_timeout), \
                    patch.object(self.page, 'goto', AsyncMock(side_effect=AssertionError('no main-page navigation'))):
                result, path = await collector.reinspect_existing_sources(inspect_config, original, page=self.page)
            self.assertEqual(original.read_bytes(), original_raw)
            self.assertEqual(json.loads(original.read_bytes())['collection_status'], 'partial')
            self.assertEqual(result['mode'], 'inspect_existing_answer')
            self.assertFalse(result['question_submission_attempted'])
            self.assertFalse(result['inspection_provenance']['question_sent'])
            self.assertTrue(result['inspection_provenance']['original_result_unchanged'])
            self.assertEqual(result['collection_status'], 'success', result.get('failure_reason'))
            self.assertEqual(result['source_context_binding']['status'], 'verified')
            self.assertEqual(result['citations'][0]['url'], SOURCE)
            self.assertEqual(result['citations'][0]['evidence_file'], 'citation-1.json')
            self.assertTrue((path.parent/'citation-1.json').is_file())
            self.assertNotIn('citation-1.png', result['screenshots'])
            self.assertFalse((path.parent/'citation-1.png').exists())
            inspection._evidence_manifest(result, path, root/'inspections')
            inspection._context_manifest(result, path, root/'inspections')
            self.assertEqual(await self.page.evaluate('window.sourceClicks'), 1)


if __name__ == '__main__':
    unittest.main()
