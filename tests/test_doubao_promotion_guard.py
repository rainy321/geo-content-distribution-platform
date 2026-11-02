"""Offline actionability and promotion fixtures; no live account or request."""
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import doubao_source_collector as collector
from services.doubao_submission_transport import submission_text


FIXTURE = Path(__file__).parent / 'fixtures/doubao/collector-download-promotion.html'


class PromotionGuardDOMTests(unittest.IsolatedAsyncioTestCase):
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
        self.question = '合成任务：只返回JSON。\n{"literal":true}'
        self.sent = submission_text(self.question, 'verbatim_fenced')
        self.config = collector.CollectorConfig(question=self.question, submission_format='verbatim_fenced')

    async def prepare(self, script=None):
        await self.page.set_content(FIXTURE.read_text(encoding='utf-8'))
        if script:
            await self.page.evaluate(script)
        await self.page.locator('textarea').fill(self.sent)
        self.baseline = await collector.read_snapshot(self.page, self.config)
        return await collector._fenced_send_control(self.page, self.config, self.root)

    async def ready(self, control):
        return await collector._fenced_send_ready(self.page, self.config, self.root,
                                                control, self.sent, self.baseline)

    async def counts(self):
        return await self.page.evaluate('({send:sendClicks, close:closeClicks, download:downloadClicks, enter:enterPresses})')

    async def test_known_promotion_closes_once_then_rechecks_input_before_one_normal_send(self):
        control = await self.prepare()
        ready = await self.ready(control)
        self.assertEqual(await self.counts(), {'send': 0, 'close': 1, 'download': 0, 'enter': 0})
        proof = json.loads((self.root / 'submission-actionability.json').read_text(encoding='utf-8'))
        self.assertEqual([row['passed'] for row in proof['trial_checks']], [False, True])
        self.assertTrue(proof['editor_rechecked'])
        self.assertTrue(proof['ready'])
        self.assertEqual(await self.page.locator('textarea').input_value(), self.sent)
        self.assertEqual((await collector.read_snapshot(self.page, self.config))['client_mode'], 'consumer')
        await ready.click(timeout=1000)
        self.assertEqual(await self.counts(), {'send': 1, 'close': 1, 'download': 0, 'enter': 0})

    async def test_unblocked_button_trials_do_not_send_or_touch_other_controls(self):
        control = await self.prepare("document.querySelector('#promotion-layer').remove()")
        await self.ready(control)
        self.assertEqual(await self.counts(), {'send': 0, 'close': 0, 'download': 0, 'enter': 0})
        proof = json.loads((self.root / 'submission-actionability.json').read_text(encoding='utf-8'))
        self.assertEqual(proof['trial_checks'], [{'passed': True}])
        self.assertFalse(proof['promotion_close_attempted'])

    async def test_unknown_overlay_captcha_and_login_never_receive_a_close_or_send(self):
        variants = (
            "document.querySelector('h2').textContent='未知活动窗口'",
            "document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend','<div id=captcha-modal>请完成安全验证</div>')",
            "document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend','<input type=tel placeholder=手机号登录>')",
            "document.querySelector('h2').textContent+='，请确认授权'",
        )
        for script in variants:
            with self.subTest(script=script):
                control = await self.prepare(script)
                with self.assertRaisesRegex(ValueError, 'submission_send_button_blocked_not_submitted'):
                    await self.ready(control)
                self.assertEqual(await self.counts(), {'send': 0, 'close': 0, 'download': 0, 'enter': 0})

    async def test_duplicate_hidden_and_unknown_close_controls_are_not_clicked(self):
        variants = (
            "document.querySelector('[role=dialog]').append(document.querySelector('[data-testid=promotion-close]').cloneNode(true))",
            "document.querySelector('[data-testid=promotion-close]').hidden=true",
            "document.querySelector('[data-testid=promotion-close]').style.opacity='0'",
            "document.querySelector('[data-testid=promotion-close]').removeAttribute('aria-label'); document.querySelector('[data-testid=promotion-close]').removeAttribute('data-testid'); document.querySelector('.promotion-close').className='unrecognized-control'; document.querySelector('.unrecognized-control').textContent='其他操作'",
        )
        for script in variants:
            with self.subTest(script=script):
                control = await self.prepare(script)
                with self.assertRaisesRegex(ValueError, 'submission_send_button_blocked_not_submitted'):
                    await self.ready(control)
                self.assertEqual(await self.counts(), {'send': 0, 'close': 0, 'download': 0, 'enter': 0})

    async def test_after_close_changed_input_mode_or_captcha_stops_without_restoring_or_sending(self):
        for mutation in ('editor', 'mode', 'captcha'):
            with self.subTest(mutation=mutation):
                control = await self.prepare(f"window.closeMutation='{mutation}'")
                with self.assertRaisesRegex(ValueError, 'editor_readback_mismatch_not_submitted|submission_context_changed_not_submitted'):
                    await self.ready(control)
                self.assertEqual(await self.counts(), {'send': 0, 'close': 1, 'download': 0, 'enter': 0})
                if mutation == 'editor':
                    self.assertEqual(await self.page.locator('textarea').input_value(), '外部改变的原文')

    async def test_failed_close_or_second_overlay_is_never_clicked_again_or_bypassed(self):
        for mutation in ('no-effect', 'another-overlay'):
            with self.subTest(mutation=mutation):
                control = await self.prepare(f"window.closeMutation='{mutation}'")
                with self.assertRaisesRegex(ValueError, 'submission_send_button_blocked_not_submitted'):
                    await self.ready(control)
                self.assertEqual(await self.counts(), {'send': 0, 'close': 1, 'download': 0, 'enter': 0})
                proof = json.loads((self.root / 'submission-actionability.json').read_text(encoding='utf-8'))
                self.assertEqual([row['passed'] for row in proof['trial_checks']], [False, False])
                self.assertFalse(proof['ready'])

    async def test_actionability_failure_leaves_collector_attempt_false_and_persists_evidence(self):
        await self.prepare("document.querySelector('h2').textContent='未知弹窗，不应关闭'")
        context = SimpleNamespace(new_page=AsyncMock(return_value=self.page), browser=None, close=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield SimpleNamespace()
        events = []
        config = collector.CollectorConfig(question=self.question, submission_format='verbatim_fenced',
            profile_dir=self.root / 'profile', output_root=self.root / 'evidence', timeout_seconds=1)
        with patch.object(collector, 'launch_context', AsyncMock(return_value=context)), \
             patch.object(collector, '_navigate_to_doubao', AsyncMock()):
            result, path = await collector.collect_doubao_sources(config, playwright_factory=factory, progress=events.append)
        self.assertFalse(result['question_submission_attempted'])
        self.assertFalse(result['question_submitted'])
        self.assertEqual(result['failure_reason'], 'submission_send_button_blocked_not_submitted')
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertEqual(result['failure_stage'], 'submission_control')
        self.assertNotIn('question_submission_started', [event['event'] for event in events])
        self.assertEqual(await self.counts(), {'send': 0, 'close': 0, 'download': 0, 'enter': 0})
        self.assertTrue(path.is_file())
        self.assertTrue((path.parent / 'submission-actionability.json').is_file())
        self.assertTrue((path.parent / 'failure.png').is_file())


if __name__ == '__main__':
    unittest.main()
