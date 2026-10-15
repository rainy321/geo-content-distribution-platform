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
