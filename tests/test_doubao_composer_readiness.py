"""Consumer hydration regression: only synthetic DOM and isolated test files."""
import hashlib
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import doubao_source_collector as collector


QUESTION = '懂AI中转站是什么？它的官方网站和实际服务入口分别在哪里，由哪家公司运营，主要面向谁、提供哪些功能？请给出实际查到的公开来源；没有找到或无法核验的内容请明确说明。'
FIXTURE = Path(__file__).parent / 'fixtures/doubao/collector-download-promotion.html'


def state(**changes):
    return {'page_url': 'https://www.doubao.com/chat/', 'client_mode': 'consumer',
            'model_label': '快速', 'chat_interface_ready': True,
            'user_message_count': 0, 'assistant_count': 0,
            'login_required': False, 'challenge': False, **changes}


class ConsumerHydrationDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        self.browser = await runtime.chromium.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Fulfill the fixed origin locally, so its actual DOM reader also checks
        # the normal HTTPS route. No network or persistent account is involved.
        await self.page.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8', body=FIXTURE.read_text(encoding='utf8')))
        await self.page.goto('https://www.doubao.com/chat/')

    async def test_legacy_custom_session_keeps_unknown_model_without_claiming_identity(self):
        await self.page.evaluate('''() => {
            document.querySelector('[data-testid="chat_input_action_model"]').remove();
            document.querySelector('#promotion-layer').remove();
            document.querySelector('#flow-end-msg-send').addEventListener('click', () => {
                const user = document.createElement('div'); user.dataset.messageRole = 'user';
                user.textContent = document.querySelector('textarea').value;
                const assistant = document.createElement('div'); assistant.dataset.messageRole = 'assistant';
                assistant.innerHTML = '<div data-testid="message_text_content">这是合成兼容测试的完整回答。</div><button data-testid="message_action_copy">复制</button>';
                document.body.append(user, assistant);
            });
        }''')
        context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        config = collector.CollectorConfig(question='合成兼容问题', client_mode='consumer',
            profile_dir=self.root/'profile', output_root=self.root/'evidence', stable_seconds=.05, timeout_seconds=4)
        self.assertFalse(config.strict_client_mode)
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', AsyncMock()):
            result, path = await collector.collect_doubao_sources(config, playwright_factory=factory)
        self.assertEqual(result['collection_status'], 'success', result)
        self.assertTrue(result['question_submitted'])
        self.assertTrue(result['answer_completed'])
        self.assertIsNone(result['environment']['model_label'])
        self.assertEqual(result['environment']['account_identity_status'], 'unverified')
        self.assertFalse((path.parent/'composer-readiness.json').exists())
        self.assertEqual(await self.page.evaluate('({send:sendClicks,close:closeClicks,enter:enterPresses})'),
                         {'send': 1, 'close': 0, 'enter': 0})

    async def test_delayed_model_hydration_is_ready_before_fill_and_one_normal_send_after_ad(self):
        await self.page.evaluate('''() => {
            document.querySelector('[data-testid="chat_input_action_model"]').remove();
            const layer = document.querySelector('#promotion-layer'); layer.hidden = true;
            const create = document.createElement('button'); create.textContent = '新对话';
            create.addEventListener('click', () => {
                layer.hidden = false;
                setTimeout(() => {
                    const model = document.createElement('button');
                    model.dataset.testid = 'chat_input_action_model'; model.textContent = '快速';
                    document.querySelector('textarea').before(model);
                }, 600);
            });
            document.body.prepend(create);
            document.querySelector('#flow-end-msg-send').addEventListener('click', () => {
                const user = document.createElement('div'); user.dataset.messageRole = 'user';
                user.textContent = document.querySelector('textarea').value;
                const assistant = document.createElement('div'); assistant.dataset.messageRole = 'assistant';
                assistant.innerHTML = '<div data-testid="message_text_content">这是合成测试的完整回答。</div><button data-testid="message_action_copy">复制</button>';
                document.body.append(user, assistant);
            });
        }''')
        before = await collector.read_snapshot(self.page, collector.CollectorConfig(strict_client_mode=True))
        self.assertIsNone(before['model_label'])
        self.assertFalse(before['chat_interface_ready'])
        context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        config = collector.CollectorConfig(question=QUESTION, client_mode='consumer', strict_client_mode=True,
            profile_dir=self.root/'profile', output_root=self.root/'evidence', stable_seconds=.05, timeout_seconds=4)
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', AsyncMock()):
            result, path = await collector.collect_doubao_sources(config, playwright_factory=factory)
        counts = await self.page.evaluate('({send:sendClicks,close:closeClicks,download:downloadClicks,enter:enterPresses})')
        self.assertEqual(counts, {'send': 1, 'close': 1, 'download': 0, 'enter': 0}, result)
        self.assertTrue(result['question_submission_attempted'])
        self.assertTrue(result['question_submitted'])
        self.assertTrue(result['answer_completed'])
        self.assertEqual(result['question'], QUESTION)
        self.assertEqual(result['environment']['model_label'], '快速')
        self.assertEqual(result['submission_transport']['submitted_text_sha256'], hashlib.sha256(QUESTION.encode()).hexdigest())
        self.assertEqual(result['submission_receipt']['submission_attempts'], 1)
        proof = json.loads((path.parent/'composer-readiness.json').read_text(encoding='utf8'))
        self.assertTrue(proof['ready'])
        self.assertGreaterEqual(len(proof['observations']), 3)
        checkpoint = json.loads((path.parent/'before.json').read_text(encoding='utf8'))
        self.assertEqual(checkpoint['model_label'], '快速')
        self.assertTrue(checkpoint['chat_interface_ready'])
        self.assertEqual((checkpoint['user_message_count'], checkpoint['assistant_count']), (0, 0))

    async def test_real_known_model_change_after_promotion_still_blocks_without_send(self):
        await self.page.locator('textarea').fill(QUESTION)
        await self.page.evaluate('''() => {
            document.querySelector('[data-testid="promotion-close"]').addEventListener('click', () => {
                document.querySelector('[data-testid="chat_input_action_model"]').textContent = '深度思考';
            });
        }''')
        config = collector.CollectorConfig(question=QUESTION, strict_client_mode=True)
        baseline = await collector.read_snapshot(self.page, config)
        control = await collector._fenced_send_control(self.page, config, self.root)
        with self.assertRaisesRegex(ValueError, 'submission_context_changed_not_submitted'):
            await collector._fenced_send_ready(self.page, config, self.root, control, QUESTION, baseline)
        self.assertEqual(await self.page.evaluate('({send:sendClicks,close:closeClicks,enter:enterPresses})'),
                         {'send': 0, 'close': 1, 'enter': 0})
        proof = json.loads((self.root/'submission-actionability.json').read_text(encoding='utf8'))
        self.assertFalse(proof['context_guard']['model_label_match'])
        self.assertEqual(proof['context_guard']['baseline']['model_label'], '快速')
        self.assertEqual(proof['context_guard']['current']['model_label'], '深度思考')


class ConsumerReadinessStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = collector.CollectorConfig(strict_client_mode=True)

    async def settle(self, initial, next_states):
        with patch.object(collector, 'read_snapshot', AsyncMock(side_effect=next_states)) as read, \
             patch.object(collector.asyncio, 'sleep', AsyncMock()):
            value = await collector._settle_consumer_composer(self.page if hasattr(self, 'page') else object(),
                                                             self.config, initial, self.root)
        return value, read

    async def test_partial_context_needs_two_equal_complete_model_observations(self):
        ready = state()
        result, read = await self.settle(state(model_label=None, chat_interface_ready=False),
                                        [state(chat_interface_ready=False), ready, ready])
        self.assertEqual(result, ready)
        self.assertEqual(read.await_count, 3)
        proof = json.loads((self.root/'composer-readiness.json').read_text(encoding='utf8'))
        self.assertTrue(proof['ready'])
        self.assertEqual(proof['stable_complete_observations'], 2)

    async def test_partial_context_never_ready_exhausts_bounded_poll_without_filling_or_sending(self):
        partial = state(model_label=None, chat_interface_ready=False)
        with patch.object(collector, 'read_snapshot', AsyncMock(return_value=partial)) as read, \
             patch.object(collector.asyncio, 'sleep', AsyncMock()), \
             self.assertRaisesRegex(ValueError, 'submission_composer_not_ready_not_submitted'):
            await collector._settle_consumer_composer(object(), self.config, partial, self.root)
        self.assertEqual(read.await_count, 12)
        proof = json.loads((self.root/'composer-readiness.json').read_text(encoding='utf8'))
        self.assertFalse(proof['ready'])
        self.assertEqual(proof['stop_reason'], 'readiness_timeout')

    async def test_auth_challenge_or_wrong_mode_returns_immediately_for_existing_fail_closed_flow(self):
        for changes in ({'challenge': True}, {'login_required': True}, {'client_mode': 'work'}):
            with self.subTest(changes=changes):
                initial = state(**changes)
                result, read = await self.settle(initial, [])
                self.assertEqual(result, initial)
                read.assert_not_awaited()

    async def test_navigation_counts_or_known_model_change_during_readiness_never_rebaseline(self):
        for changes in ({'page_url': 'https://www.doubao.com/chat/other'},
                        {'user_message_count': 1}, {'assistant_count': 1}, {'model_label': '深度思考'}):
            with self.subTest(changes=changes), \
                 patch.object(collector, 'read_snapshot', AsyncMock(return_value=state(**changes))), \
                 patch.object(collector.asyncio, 'sleep', AsyncMock()), \
                 self.assertRaisesRegex(ValueError, 'submission_context_changed_not_submitted'):
                await collector._settle_consumer_composer(object(), self.config,
                                                         state(chat_interface_ready=False), self.root)


if __name__ == '__main__':
    unittest.main()
