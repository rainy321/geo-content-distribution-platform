import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app import default_data_directory, migrate_legacy_desktop_data
from services.desktop_geo_jobs import DesktopGeoJobs
from services.desktop_task_store import DesktopTaskStore, now
from services.doubao_source_collector import CollectorConfig, collect_doubao_sources
from services.geo_test_runner import write_json
from tests.test_geo_tests import plan


class TestDesktopPersistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_failed_task_survives_restart_without_rerunning(self):
        calls = []
        async def fail():
            calls.append('called')
            raise ValueError('合成出题失败记录')
        first = DesktopGeoJobs(self.root)
        first._start('plan', fail, client_name='合成客户')
        first.worker.join(timeout=3)
        identifier = first.get_task()['id']
        second = DesktopGeoJobs(self.root)
        self.assertEqual(second.get_task()['status'], 'failed')
        self.assertEqual(second.get_task()['id'], identifier)
        self.assertTrue(second.get_task()['restored_from_disk'])
        self.assertEqual(second.list_tasks()[0]['client_name'], '合成客户')
        self.assertEqual(calls, ['called'])

    def test_completed_plan_and_draft_survive_restart_without_api_credentials(self):
        value = plan()
        first = DesktopGeoJobs(self.root)
        async def finish():
            return {'plan': value}
        first._start('plan', finish, client_name=value['profile']['name'])
        first.worker.join(timeout=3)
        first.save_workspace_state({'company': '合成自动保存公司', 'case_count': 4, 'plan': value, 'api_key': 'must-not-be-persisted'})
        second = DesktopGeoJobs(self.root)
        self.assertEqual(second.get_task()['result']['plan']['plan_sha256'], value['plan_sha256'])
        self.assertEqual(second.get_workspace_state()['company'], '合成自动保存公司')
        self.assertNotIn('api_key', second.get_workspace_state())

    def test_running_state_becomes_review_needed_without_replay(self):
        value = plan()
        run = self.root / 'geo-tests' / 'interrupted'
        state = {'run_id': 'synthetic-interruption', 'status': 'running', 'cases': {
            item['id']: {'status': 'running' if index == 0 else 'pending'} for index, item in enumerate(value['cases'])}}
        write_json(run / 'plan.json', value)
        write_json(run / 'state.json', state)
        DesktopTaskStore(self.root).save({'id': 'interrupted', 'kind': 'run', 'status': 'running', 'created_at': now(), 'run_dir': str(run)})
        restored = DesktopGeoJobs(self.root)
        self.assertEqual(restored.get_task()['status'], 'needs_review')
        self.assertIsNone(restored.worker)
        report = json.loads((run / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['stop_reason'], 'application_closed_submission_unknown')
        self.assertEqual(report['cohorts']['discovery']['answers_evaluable'], 0)

    def test_old_failed_planner_evidence_is_recovered_once(self):
        path = self.root / 'geo-tests/desktop-plans/old/plan-planner-evidence/attempt/result.json'
        write_json(path, {'question': '合成测试\n{"name":"合成旧客户"}', 'timestamp': '2026-10-14T23:00:00+08:00',
                          'failure_reason': 'playwright_not_installed', 'question_submission_attempted': False})
        first = DesktopTaskStore(self.root)
        self.assertEqual(len(first.records()), 1)
        self.assertEqual(first.records()[0]['client_name'], '合成旧客户')
        self.assertEqual(first.records()[0]['status'], 'failed')
        self.assertIn('没有发送问题', first.records()[0]['message'])
        self.assertEqual(len(DesktopTaskStore(self.root).records()), 1)

    def test_data_location_is_independent_of_packaged_launcher_appdata(self):
        with patch('desktop_app.Path.home', return_value=self.root), patch.dict(os.environ, LOCALAPPDATA=str(self.root/'redirected')):
            self.assertEqual(default_data_directory(), self.root/'.geo-workbench')

    def test_legacy_migration_preserves_conflicts_and_original_files(self):
        user = self.root/'user'
        source = user/'AppData/Local/Tongyuye/GEO_AI_Workbench'
        destination = user/'.geo-workbench'
        original = source/'geo-tests/client/run/report.json'
        write_json(original, {'original': True})
        target = destination/'geo-tests/client/run/report.json'
        write_json(target, {'newer': True})
        with patch('desktop_app.Path.home', return_value=user), patch.dict(os.environ, LOCALAPPDATA=str(user/'AppData/Local')):
            migrate_legacy_desktop_data(destination)
        self.assertEqual(json.loads(target.read_text()), {'newer': True})
        self.assertTrue(original.exists())
        backups = list((destination/'recovery').glob('*/geo-tests/client/run/report.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), {'original': True})


class TestRuntimeFailureReason(unittest.IsolatedAsyncioTestCase):
    async def test_missing_native_dependency_is_not_reported_as_missing_playwright(self):
        class BrokenContext:
            async def __aenter__(self):
                raise ModuleNotFoundError('synthetic missing dependency', name='greenlet._greenlet')
            async def __aexit__(self, *_):
                pass
        with tempfile.TemporaryDirectory() as directory:
            config = CollectorConfig(question='合成依赖故障测试', output_root=Path(directory)/'evidence', profile_dir=Path(directory)/'profile')
            result, _ = await collect_doubao_sources(config, playwright_factory=BrokenContext)
            self.assertEqual(result['failure_reason'], 'browser_runtime_dependency_missing:greenlet._greenlet')
            self.assertFalse(result['question_submission_attempted'])
