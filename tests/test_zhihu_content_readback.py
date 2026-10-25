"""Input/readback checks with a local controlled Draft-like editor, never an account."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from services.zhihu_publish_flow import ZhihuEditorNotReadyError
from uploader.zhihu_uploader.main import ZhiHuArticle, ZHIHU_AI_CREATION_STATEMENT


BODY = '第一段包含中文、API 与标点。\n\n## 两层检查\n第三段：保留  多个空格和编号 123。'
TITLE = '模型服务选型说明'


DRAFT_FIXTURE = '''<!doctype html><meta charset="utf-8">
<textarea placeholder="请输入标题"></textarea>
<div class="public-DraftEditor-content" contenteditable="true" role="textbox"
     style="min-height:160px;white-space:pre-wrap;border:1px solid #888"></div>
<script>
(() => {
window.model = '';
window.inputEvents = [];
const editor = document.querySelector('.public-DraftEditor-content');
function render() {
    const contents = document.createElement('div');
    contents.dataset.contents = 'true';
    for (const text of window.model.split('\\n')) {
        const block = document.createElement('div');
        block.dataset.block = 'true';
        block.className = 'Editable-unstyled';
        const line = document.createElement('div');
        line.className = 'public-DraftStyleDefault-block';
        const span = document.createElement('span');
        span.dataset.text = 'true';
        if (text) span.textContent = text;
        else {const br = document.createElement('br');br.dataset.text = 'true';span.append(br);}
        line.append(span);block.append(line);contents.append(block);
    }
    editor.replaceChildren(contents);
    const range = document.createRange();
    range.selectNodeContents(editor.lastElementChild.lastElementChild);
    range.collapse(false);
    const selection = getSelection();selection.removeAllRanges();selection.addRange(range);
}
window.setModel = text => {window.model=text;render();};
editor.addEventListener('beforeinput', event => {
    window.inputEvents.push({type:event.inputType,trusted:event.isTrusted,data:event.data});
    if (!event.isTrusted) return;
    if (event.inputType==='insertText') {
        event.preventDefault();window.model += event.data || '';render();
    } else if (event.inputType==='insertParagraph' || event.inputType==='insertLineBreak') {
        event.preventDefault();window.model += '\\n';render();
    } else if (event.inputType==='deleteContentBackward') {
        event.preventDefault();window.model=window.model.slice(0,-1);render();
    }
});
render();
})();
</script>'''


class InputKeyboard:
    """Represent normal native input and no-op/partial platform handling."""
    def __init__(self, page, owner, mode):
        self.page = page
        self.owner = owner
        self.mode = mode
        self.insertions = []
        self.keys = []

    async def press(self, key, **kwargs):
        self.keys.append(key)
        if key.lower() in ('control+v', 'control+shift+v'):
            raise AssertionError('Writer input must not depend on clipboard paste')
        await self.page.keyboard.press(key, **kwargs)

    async def insert_text(self, text):
        self.insertions.append(text)
        if self.mode == 'full':
            await self.page.keyboard.insert_text(text)
        elif self.mode == 'partial':
            await self.page.keyboard.insert_text(text[:12])
        elif self.mode == 'polluted':
            await self.page.keyboard.insert_text(text+'额外污染')

    async def type(self, text, **kwargs):
        raise AssertionError('A failed native insertion must not be replayed by typing')

    def __getattr__(self, name):
        return getattr(self.page.keyboard, name)


class InputPage:
    def __init__(self, page, mode='full'):
        self.page = page
        self.keyboard = InputKeyboard(page, self, mode)
        self.context = SimpleNamespace(grant_permissions=AsyncMock(
            side_effect=AssertionError('Normal editor input needs no clipboard permissions')))
        self.goto = AsyncMock()
        self.url = 'https://zhuanlan.zhihu.com/write'

    async def evaluate(self, expression, arg=None):
        if 'navigator.clipboard' in str(expression):
            raise AssertionError('Writer input must not use the clipboard API')
        return await self.page.evaluate(expression, arg)

    def __getattr__(self, name):
        return getattr(self.page, name)


class ZhihuContentReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.set_content(DRAFT_FIXTURE)
        self.article = ZhiHuArticle(TITLE, BODY, [], 0, 'unused-local-session.json',
                                   creation_statement=ZHIHU_AI_CREATION_STATEMENT)

    async def observed_body(self):
        return await self.article._read_body_text(self.page.locator('.public-DraftEditor-content'))

    async def test_single_native_input_preserves_controlled_draft_like_multiline_content(self):
        page = InputPage(self.page, 'full')
        await self.article.fill_body(page)
        self.assertEqual(await self.observed_body(), BODY)
        self.assertEqual(page.keyboard.insertions, [BODY])
        self.assertEqual(await self.page.evaluate('window.model'), BODY)
        self.assertEqual(page.keyboard.keys, [])
        events = await self.page.evaluate('window.inputEvents')
        self.assertTrue(events)
        self.assertTrue(all(event['trusted'] for event in events))
        self.assertTrue(any(event['type']=='insertText' for event in events))

    async def test_noop_input_stops_without_replay_or_clipboard_fallback(self):
        page = InputPage(self.page, 'noop')
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article.fill_body(page)
        self.assertEqual(await self.observed_body(), '')
        self.assertEqual(page.keyboard.insertions, [BODY])
        self.assertEqual(page.keyboard.keys, [])

    async def test_partial_or_polluted_input_is_not_cleared_or_replayed(self):
        for mode, expected in (('partial', BODY[:12]), ('polluted', BODY+'额外污染')):
            with self.subTest(mode=mode):
                await self.page.set_content(DRAFT_FIXTURE)
                page = InputPage(self.page, mode)
                with self.assertRaises(ZhihuEditorNotReadyError):
                    await self.article.fill_body(page)
                self.assertEqual(await self.observed_body(), expected)
                self.assertEqual(await self.page.evaluate('window.model'), expected)
                self.assertEqual(page.keyboard.insertions, [BODY])
                self.assertEqual(page.keyboard.keys, [])

    async def test_existing_nonempty_body_is_rejected_before_any_input(self):
        await self.page.evaluate('text=>window.setModel(text)', '已有草稿，需要保留。')
        page = InputPage(self.page)
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article.fill_body(page)
        self.assertEqual(await self.observed_body(), '已有草稿，需要保留。')
        self.assertEqual(page.keyboard.keys, [])
        self.assertEqual(page.keyboard.insertions, [])

    async def test_existing_nonempty_title_is_rejected_without_overwrite(self):
        await self.page.get_by_placeholder('请输入标题').fill('已有标题')
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article.fill_title(InputPage(self.page))
        self.assertEqual(await self.page.get_by_placeholder('请输入标题').input_value(), '已有标题')

    async def test_truncated_title_never_counts_as_successful_input(self):
        await self.page.get_by_placeholder('请输入标题').evaluate('e=>e.maxLength=2')
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article.fill_title(InputPage(self.page))
        self.assertEqual(await self.page.get_by_placeholder('请输入标题').input_value(), TITLE[:2])

    async def test_later_body_pollution_is_rejected_by_final_content_verification(self):
        page = InputPage(self.page, 'full')
        await self.article.fill_title(page)
        await self.article.fill_body(page)
        await self.page.locator('.public-DraftEditor-content').click()
        await self.page.keyboard.insert_text('后续意外附加')
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article._verify_content(page)
        self.assertEqual(await self.observed_body(), BODY+'后续意外附加')
        self.assertEqual(page.keyboard.insertions, [BODY])

    async def test_partial_body_stops_real_upload_sequence_before_statement_or_publish(self):
        page = InputPage(self.page, 'partial')
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        self.article.apply_creation_statement = AsyncMock()
        self.article.click_publish = AsyncMock()
        with self.assertRaises(ZhihuEditorNotReadyError):
            await self.article.upload(runtime)
        self.article.apply_creation_statement.assert_not_awaited()
        self.article.click_publish.assert_not_awaited()
        self.assertEqual(await self.observed_body(), BODY[:12])
        self.assertEqual(page.keyboard.insertions, [BODY])
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_existing_draft_blocks_upload_before_changing_either_field(self):
        for original_title, original_body in (('已有标题', ''), ('', '应保留的现有正文')):
            with self.subTest(title=original_title, body=original_body):
                await self.page.set_content(DRAFT_FIXTURE)
                await self.page.get_by_placeholder('请输入标题').fill(original_title)
                await self.page.evaluate('text=>window.setModel(text)', original_body)
                page = InputPage(self.page)
                context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
                runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
                self.article.apply_creation_statement = AsyncMock()
                self.article.click_publish = AsyncMock()
                with self.assertRaises(ZhihuEditorNotReadyError):
                    await self.article.upload(runtime)
                self.assertEqual(await self.page.get_by_placeholder('请输入标题').input_value(), original_title)
                self.assertEqual(await self.observed_body(), original_body)
                self.assertEqual(page.keyboard.insertions, [])
                self.assertEqual(page.keyboard.keys, [])
                self.article.apply_creation_statement.assert_not_awaited()
                self.article.click_publish.assert_not_awaited()

    async def test_readback_preserves_blank_lines_and_only_normalizes_rendered_spaces(self):
        await self.page.evaluate('text=>window.setModel(text)', '甲\r\n\r\n乙\u00a0\u00a0丙')
        self.assertEqual(await self.observed_body(), '甲\n\n乙  丙')


if __name__ == '__main__':
    unittest.main()
