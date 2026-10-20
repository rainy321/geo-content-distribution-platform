import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_runner import run_test_plan
from tests.test_geo_tests import plan, answer


class TestNativeGeoJobs(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def wait_finished(self):
        self.jobs.worker.join(timeout=5)
        self.assertFalse(self.jobs.worker.is_alive())
        return self.jobs.get_task()

    def test_public_chat_and_work_keep_separate_sessions_without_copying_connectors(self):
        work = self.root/'doubao/browser-profile'
        work.mkdir(parents=True)
        marker = work/'existing-work-session.json'
        marker.write_text('{"synthetic_work_connector":true}', encoding='utf-8')
        public_config = self.jobs._config({'client_mode': 'consumer'})
        work_config = self.jobs._config({'client_mode': 'work'})
        self.assertEqual(public_config.profile_dir, self.root/'doubao/consumer-browser-profile')
        self.assertEqual(work_config.profile_dir, work)
        self.assertEqual(self.jobs._config({}).profile_dir, public_config.profile_dir)
        self.assertFalse((public_config.profile_dir/marker.name).exists())
        self.assertEqual(marker.read_text(encoding='utf-8'), '{"synthetic_work_connector":true}')

    def test_invalid_plan_is_rejected_without_starting_browser(self):
        value = plan()
        value['cases'][0]['question'] += '已修改'
        self.assertIn('error', self.jobs.start_run(value, {'client_mode': 'consumer'}))
        self.assertEqual(self.jobs.get_task()['status'], 'idle')

    def test_concurrent_task_is_rejected_and_stop_cancels_wait(self):
        entered = threading.Event()
        async def pending():
            entered.set()
            await asyncio.sleep(3600)
        self.jobs._start('synthetic-test', pending)
        self.assertTrue(entered.wait(timeout=3))
        self.assertIn('error', self.jobs._start('second-synthetic-test', pending))
        self.jobs.stop_task()
        self.assertEqual(self.wait_finished()['status'], 'stopped')

    def test_manual_login_has_no_automatic_timeout_and_remains_cancellable(self):
        entered, closed = threading.Event(), threading.Event()
        budgets = []
        async def manual_login(_config, wait_seconds):
            budgets.append(wait_seconds)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        with patch('services.desktop_geo_jobs.open_doubao_session', manual_login):
            self.jobs.open_login({'client_mode': 'consumer'})
            self.assertTrue(entered.wait(timeout=3))
            self.assertEqual(budgets, [None])
            self.assertEqual(self.jobs.get_task()['status'], 'running')
            self.jobs.stop_task()
            self.assertEqual(self.wait_finished()['status'], 'stopped')
        self.assertTrue(closed.is_set())

    def test_native_run_returns_real_runner_report_with_synthetic_answers(self):
        async def collect(config):
            return answer(config.question, response='合成桌面桥接验收回答，并非客户曝光数据。'), self.root / 'synthetic-result.json'
        async def no_wait(_):
            pass
        async def bridge(value, config, **kwargs):
            return await run_test_plan(value, config, collect=collect, sleep=no_wait, **kwargs)
        with patch('services.desktop_geo_jobs.run_test_plan', bridge):
            self.jobs.start_run(plan(), {'client_mode': 'consumer'})
            result = self.wait_finished()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['report']['status'], 'completed')
        self.assertEqual(len(result['result']['report']['cases']), 4)
        self.assertTrue((Path(result['run_dir']) / 'report.json').is_file())

    def test_native_retest_keeps_case_scoped_source_progress_without_resubmitting(self):
        value=plan()
        calls, observations=[],[]
        async def collect(config, *, progress):
            calls.append(config.question)
            progress({'event':'answer_completed','state':'extracting_sources','question_submitted':True})
            progress({'event':'source_progress','phase':'inline_citations','processed':3,'total':28,'url':'https://not-for-task.example'})
            observations.append(self.jobs.get_task())
            return answer(config.question),self.root/'synthetic-result.json'
        async def no_wait(_):pass
        async def bridge(plan_value, config, **kwargs):
            return await run_test_plan(plan_value,config,collect=collect,sleep=no_wait,**kwargs)
        cycle={'baseline':{'execution':{'client_mode':'consumer'},'cases':value['cases']},'test_plan':value}
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle',return_value=cycle), \
             patch('services.desktop_geo_jobs.geo_content_store.attach_retest') as attach, \
             patch('services.desktop_geo_jobs.run_test_plan',bridge):
            self.jobs.start_research_retest('synthetic-cycle')
            result=self.wait_finished()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(calls,[case['question'] for case in value['cases']])
        self.assertEqual([item['progress']['case_id'] for item in observations],[case['id'] for case in value['cases']])
        self.assertTrue(all('已检查 3/28' in item['message'] for item in observations))
        self.assertNotIn('not-for-task.example',json.dumps(observations))
        attach.assert_called_once()

    def test_cancel_preserves_unknown_submission_as_needs_review(self):
        entered = threading.Event()
        async def pending_collect(config):
            entered.set()
            await asyncio.sleep(3600)
        async def bridge(value, config, **kwargs):
            return await run_test_plan(value, config, collect=pending_collect, **kwargs)
        with patch('services.desktop_geo_jobs.run_test_plan', bridge):
            self.jobs.start_run(plan(), {'client_mode': 'consumer'})
            self.assertTrue(entered.wait(timeout=3))
            self.jobs.stop_task()
            result = self.wait_finished()
        root = Path(result['run_dir'])
        state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'needs_review')
        self.assertEqual(state['stop_reason'], 'user_cancelled_submission_unknown')
        self.assertEqual(state['cases']['q01']['status'], 'running')
        report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['cohorts']['discovery']['answers_evaluable'], 0)
