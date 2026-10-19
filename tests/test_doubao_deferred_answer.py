"""A delegation/copy button is not a completed search answer or a negative hit."""
import copy
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.doubao_answer_state import is_deferred_answer
from services.doubao_source_collector import CollectorConfig, CompletionTracker, SubmissionReceipt, collect_doubao_sources
from services.geo_test_service import assess_answer, build_report
from services.geo_content_research import compare_reports, research_observations
from tests.test_doubao_manual_wait import CANONICAL, FakePage, snapshot
from tests.test_geo_tests import answer, plan, profile


NOTICE = ('我来先评估这个任务：这是一次需要多渠道核验的调研任务。我先把它委托给组织者统一调度。\n'
          '已把这个调研任务委托给专门的调研代理执行了。查不到的价格、稳定性、合规性会如实标注未知。\n'
          '调研在后台进行，你可以继续问我其他问题；完成后我会把带来源标注的候选清单整理给你。')


class DeferredAssessmentTests(unittest.TestCase):
    def test_notice_copy_and_brand_in_notice_never_imply_final_answer_or_zero(self):
        self.assertTrue(is_deferred_answer(NOTICE))
        self.assertTrue(is_deferred_answer('已委托专门调研代理，后台进行，完成后整理给你。'))
        tracker = CompletionTracker(0, .1)
        page = snapshot(challenge=False, answer=NOTICE, completion_marker=True, assistant_count=1)
        self.assertFalse(tracker.observe(page, 1))
        self.assertFalse(tracker.observe(page, 100))
        for text in (NOTICE, NOTICE + '客户名称：懂AI中转站。'):
            assessment = assess_answer(profile(), answer('测试问题', response=text), '测试问题')
            self.assertEqual(assessment['answer_error'], 'background_task_not_final')
            self.assertFalse(assessment['answer_evaluable'])
            for field in ('brand_mentioned', 'official_source_present', 'official_citation_present'):
                self.assertIsNone(assessment[field])

    def test_technical_background_topic_and_completed_results_are_evaluable(self):
        for text in ('后台任务完成后如何通知用户：可以使用消息队列和回调。',
                     '我已委托调研代理执行。调研已完成，以下是最终结果：懂AI中转站的官网为公开来源。',
                     '以下是核验结果：A服务支持多模型。后台任务完成后会通知。',
                     '用户问“已委托调研代理，完成后给你”，这是占位提示的示例说明。'):
            with self.subTest(text=text):
                self.assertFalse(is_deferred_answer(text))
                self.assertTrue(assess_answer(profile(), answer('测试问题', response=text), '测试问题')['answer_evaluable'])

    def test_legacy_success_report_downgrades_with_unknown_denominator_and_raw_unchanged(self):
        test_plan = plan()
        state = {'run_id': 'synthetic-notice', 'status': 'completed', 'cases': {
            case['id']: {'status': 'success', 'result': answer(case['question'], response=NOTICE)}
            for case in test_plan['cases']}}
        original = copy.deepcopy(state)
        report = build_report(test_plan, state)
        self.assertEqual(state, original)
        self.assertEqual(report['status'], 'completed_with_gaps')
        self.assertTrue(all(row['status'] == 'deferred_answer' for row in report['cases']))
        self.assertIsNone(report['cohorts']['discovery']['brand_mention_rate'])
        self.assertEqual(report['cohorts']['discovery']['answers_evaluable'], 0)

    def test_missing_user_fallback_allows_only_one_bound_followup_of_verified_notice(self):
        page = FakePage()
        receipt = SubmissionReceipt(page, 'exact question', 0)
        receipt.attempted()
        initial = snapshot(answer=NOTICE, assistant_count=1)
        verified, _ = receipt.observe(page, receipt.question, initial)
        receipt.manual_action_seen = True
        page.navigate(CANONICAL)
        final = snapshot(page_url=CANONICAL, question_verified=False, user_message_count=0,
                         assistant_count=2, answer='Final actual result')
        self.assertFalse(receipt.observe(page, receipt.question, final)[0])
        receipt.observed_deferred(initial, verified=verified)
        self.assertTrue(receipt.observe(page, receipt.question, final)[0])
        self.assertFalse(receipt.observe(page, receipt.question, {**final, 'assistant_count': 3})[0])
        page.navigate('https://www.doubao.com/chat/111111')
        page.navigate(CANONICAL)
        self.assertFalse(receipt.observe(page, receipt.question, final)[0])

    def test_raw_old_report_import_cannot_turn_notices_into_editorial_gaps_or_feedback(self):
        test_plan = plan()
        state = {'run_id': 'synthetic-old', 'status': 'completed', 'cases': {
            case['id']: {'status': 'success', 'result': answer(case['question'])}
            for case in test_plan['cases']}}
        legacy = build_report(test_plan, state)
        for row in legacy['cases']:
            row.update(response=NOTICE, collected_at='2026-10-19T00:00:00+00:00',
                       model_label='synthetic', client_mode='consumer')
        original = copy.deepcopy(legacy)
        with self.assertRaisesRegex(ValueError, '报告没有有效回答'):
            research_observations(legacy)
        later = copy.deepcopy(legacy)
        later['run_id'] = 'synthetic-later'
        for row in later['cases']:
            row['collected_at'] = '2026-10-20T00:00:00+00:00'
        comparison = compare_reports(legacy, later)
        self.assertTrue(all(row['paired_answers'] == 0 for row in comparison['cohorts']))
        self.assertTrue(all(row['change'] is None for row in comparison['cohorts']))
        self.assertEqual(legacy, original)


class DeferredCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def collect_synthetic(self, *, final):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = FakePage()
            page.locator = Mock(return_value=SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock())))
            page.get_by_role = Mock()
            page.evaluate = AsyncMock(return_value='Synthetic browser')
            editor = SimpleNamespace(fill=AsyncMock(), press=AsyncMock(), evaluate=AsyncMock(return_value={
                'readback':'exact question', 'method':'contenteditable_inner_text_exact',
                'editor_dom':'<div contenteditable="true">exact question</div>',
                'inner_text':'exact question', 'text_content':'exact question'}))
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), browser=None, close=AsyncMock())
            @asynccontextmanager
            async def factory():
                yield SimpleNamespace()
            calls, clock, events = [0], [0], []
            async def read(*_args, **_kwargs):
                calls[0] += 1
                if calls[0] <= 2:
                    return snapshot(challenge=False, question_verified=False, user_message_count=0)
                text = '公开核验的最终回答：可比较接口兼容性、价格依据与可查来源。' if final and calls[0] >= 5 else NOTICE
                return snapshot(challenge=False, assistant_count=2 if text != NOTICE else 1,
                                answer=text, completion_marker=True)
            async def sleep(_):
                clock[0] += .5
            expand = AsyncMock(return_value=([], [], False))
            with patch('services.doubao_source_collector.launch_context', AsyncMock(return_value=context)), \
                 patch('services.doubao_source_collector._navigate_to_doubao', AsyncMock()), \
                 patch('services.doubao_source_collector._first_visible', AsyncMock(side_effect=[None, editor])), \
                 patch('services.doubao_source_collector.read_snapshot', read), \
                 patch('services.doubao_source_collector._save_checkpoint', AsyncMock()), \
                 patch('services.doubao_source_collector._expand_sources', expand), \
                 patch('services.doubao_source_collector.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                 patch('services.doubao_source_collector.asyncio.sleep', sleep):
                result, path = await collect_doubao_sources(CollectorConfig(question='exact question',
                    output_root=root/'evidence', profile_dir=root/'profile', timeout_seconds=3, stable_seconds=.1),
                    playwright_factory=factory, progress=events.append)
            self.assertEqual(editor.press.await_count, 1)
            self.assertTrue(path.is_file())
            self.assertEqual(sum(e['event']=='background_task_waiting' for e in events), 1)
            return result, events, expand.await_count

    async def test_waits_same_request_for_appended_final_message(self):
        result, events, extractions = await self.collect_synthetic(final=True)
        self.assertTrue(result['answer_completed'], result)
        self.assertEqual(result['collection_status'], 'success')
        self.assertFalse(result['deferred_answer']['pending'])
        self.assertEqual(extractions, 1)
        self.assertIn('background_task_cleared', [e['event'] for e in events])
        self.assertEqual(events[-1]['event'], 'answer_completed')
        self.assertEqual(events[-1]['state'], 'extracting_sources')
        self.assertNotIn('委托', result['response'])

    async def test_pending_timeout_is_not_success_and_never_extracts_source_zero(self):
        result, events, extractions = await self.collect_synthetic(final=False)
        self.assertFalse(result['answer_completed'])
        self.assertEqual(result['collection_status'], 'need_action')
        self.assertEqual(result['failure_reason'], 'background_task_timeout')
        self.assertIsNone(result['visible_source_count'])
        self.assertEqual(extractions, 0)
        self.assertIsNone(assess_answer(profile(), result, 'exact question')['brand_mentioned'])


if __name__ == '__main__':
    unittest.main()
