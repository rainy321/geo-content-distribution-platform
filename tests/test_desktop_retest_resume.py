"""Resume copied desktop experiments through the existing serial runner; no browser."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_retest_resume import source_manifest, file_digest
from services.geo_test_runner import run_test_plan, write_json
from services.geo_test_service import build_report, plan_fingerprint
from tests.test_geo_tests import plan, answer


NOTICE = '已为您启动调研任务。调研完成后会以飞书文档形式交付给您，无需等待。'


class DesktopRetestResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)
        (self.root/'database.db').touch()
        self.plan = plan()
        self.plan['cases'] = [self.plan['cases'][index] for index in (0, 2, 3)]
        for index, case in enumerate(self.plan['cases'], 1):
            case['id'] = f'q{index:02d}'
        self.plan['plan_sha256'] = plan_fingerprint(self.plan)
        self.source_dir = self.root/'geo-tests/desktop-runs'/('1'*32)
        self.source_dir.mkdir(parents=True)
        old_result = answer(self.plan['cases'][0]['question'], response=NOTICE,
            collection_status='failed', failure_reason='browser_error:TimeoutError',
            answer_completed=True, question_submission_attempted=True, question_submitted=True)
        evidence = self.source_dir/'evidence/q01/old/result.json'
        write_json(evidence, old_result)
        self.state = {'run_id': 'existing-logical-experiment', 'plan_sha256': self.plan['plan_sha256'],
            'status': 'stopped', 'execution': {'client_mode': 'work', 'requested_model_label': None,
                'timeout_seconds': 180, 'cooldown_seconds': 15, 'headed': True},
            'cases': {'q01': {'status': 'failed', 'result': old_result, 'result_path': str(evidence)},
                      'q02': {'status': 'pending'}, 'q03': {'status': 'pending'}}}
        report = build_report(self.plan, self.state)
        # An old cached assessment must never survive current report rebuilding.
        report['cohorts']['discovery']['answers_evaluable'] = 1
        report['cohorts']['discovery']['brand_mention_rate'] = 0
        self.source = {'id': 'saved-source', 'kind': 'run', 'status': 'failed', 'cycle_id': 'same-cycle',
            'run_dir': str(self.source_dir), 'requested_client_mode': 'work', 'requested_model_label': None,
            'result': {'report': report}}
        write_json(self.source_dir/'plan.json', self.plan)
        write_json(self.source_dir/'state.json', self.state)
        write_json(self.source_dir/'report.json', report)
        self.jobs.store.save(self.source)
        self.cycle = {'id': 'same-cycle', 'test_plan': self.plan}

    def save_state(self):
        write_json(self.source_dir/'state.json', self.state)

    def resume(self):
        return self.jobs.resume_research_retest('saved-source', 'same-cycle')

    def test_copied_resume_only_sends_pending_and_preserves_every_original_byte(self):
        before = source_manifest(self.source_dir)
        task_path = self.root/'desktop-jobs/saved-source.json'
        task_hash = file_digest(task_path)
        calls, configs = [], []
        async def collect(config, *, progress):
            calls.append(config.question)
            return answer(config.question, response='本题的公开资料核验已经完成。'), self.root/'synthetic-answer.json'
        async def no_sleep(_):
            pass
        async def runner(value, config, **kwargs):
            self.assertTrue(kwargs['resume'])
            configs.append(config)
            return await run_test_plan(value, config, collect=collect, sleep=no_sleep, **kwargs)
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
                patch('services.desktop_geo_jobs.geo_content_store.attach_retest') as attach, \
                patch('services.desktop_geo_jobs.run_test_plan', runner), \
                patch('services.desktop_geo_jobs.edge_executable', return_value='ordinary-edge.exe'), \
                patch('services.desktop_geo_jobs.chrome_executable', return_value='ordinary-chrome.exe'):
            capability = self.jobs.get_task('saved-source')['resume_research_retest']
            self.assertEqual(capability['pending_case_ids'], ['q02', 'q03'])
            self.assertTrue(capability['can_resume'])
            started = self.resume()
            self.assertEqual(started['status'], 'running')
            self.jobs.worker.join(timeout=5)
            self.assertFalse(self.jobs.worker.is_alive())
            saved = self.jobs.get_task(started['id'])
            self.assertFalse(self.jobs.get_task('saved-source')['resume_research_retest']['can_resume'])
            self.assertIn('error', self.resume())
        self.assertEqual(calls, [case['question'] for case in self.plan['cases'][1:]])
        self.assertEqual(source_manifest(self.source_dir), before)
        self.assertEqual(file_digest(task_path), task_hash)
        self.assertEqual(self.jobs.store.read('saved-source')['status'], 'failed')
        self.assertNotEqual(Path(saved['run_dir']), self.source_dir)
        self.assertEqual(saved['resumed_from_task_id'], 'saved-source')
        self.assertEqual(saved['resumed_from_manifest_sha256'], before['sha256'])
        self.assertEqual(saved['resumed_case_ids'], ['q02', 'q03'])
        report = saved['result']['report']
        self.assertEqual(report['run_id'], self.state['run_id'])
        self.assertEqual(report['status'], 'completed_with_gaps')
        self.assertFalse(report['cases'][0]['assessment']['answer_evaluable'])
        self.assertEqual(report['cohorts']['discovery']['answers_evaluable'], 0)
        self.assertIsNone(report['cohorts']['discovery']['brand_mention_rate'])
        self.assertEqual(report['cohorts']['brand_identity']['answers_evaluable'], 1)
        attach.assert_called_once()
        config = configs[0].collector
        self.assertEqual(config.profile_dir, self.root/'doubao/browser-profile')
        self.assertEqual(config.client_mode, 'work')
        self.assertIsNone(config.requested_model_label)
        self.assertIsNone(config.manual_wait_seconds)
        self.assertTrue(config.headed)
        self.assertEqual(config.executable_path, 'ordinary-edge.exe')
        self.assertEqual(config.fallback_executable_path, 'ordinary-chrome.exe')
        self.assertTrue((Path(saved['run_dir'])/'resume-source-manifest.json').is_file())

    def test_automatic_resume_keeps_consumer_profile_with_actual_work_mode(self):
        self.state['execution'].update(profile_mode='consumer', mode_policy='available_web', automation_scope='doubao_web')
        self.source.update(profile_mode='consumer', mode_policy='available_web', automation_scope='doubao_web')
        self.save_state()
        self.jobs.store.save(self.source)
        original = source_manifest(self.source_dir)
        configurations = []
        async def collect(config, **kwargs):
            return answer(config.question), self.root / 'synthetic-resumed.json'
        async def no_sleep(_):
            pass
        async def runner(value, config, **kwargs):
            configurations.append(config)
            return await run_test_plan(value, config, collect=collect, sleep=no_sleep, **kwargs)
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
             patch('services.desktop_geo_jobs.geo_content_store.attach_retest'), \
             patch('services.desktop_geo_jobs.run_test_plan', runner):
            started = self.resume()
            self.assertEqual(started['status'], 'running')
            self.jobs.worker.join(timeout=5)
            self.assertFalse(self.jobs.worker.is_alive())
            saved = self.jobs.get_task(started['id'])
        config = configurations[0]
        self.assertEqual(config.collector.profile_dir, self.root / 'doubao/consumer-browser-profile')
        self.assertEqual(config.collector.client_mode, 'work')
        self.assertTrue(config.collector.strict_client_mode)
        self.assertEqual(config.profile_mode, 'consumer')
        self.assertEqual(config.mode_policy, 'available_web')
        self.assertEqual(saved['profile_mode'], 'consumer')
        self.assertEqual(saved['mode_policy'], 'available_web')
        self.assertEqual(saved['result']['report']['execution']['profile_mode'], 'consumer')
        self.assertEqual(source_manifest(self.source_dir), original)

    def test_resume_rejects_new_session_policy_without_matching_original_task(self):
        self.state['execution'].update(profile_mode='consumer', mode_policy='available_web')
        self.save_state()
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
             patch('services.desktop_geo_jobs.run_test_plan', new_callable=AsyncMock) as run:
            self.assertIn('error', self.resume())
        run.assert_not_awaited()

    def test_pending_with_any_attempt_trace_unknown_metadata_or_receipt_is_rejected(self):
        original = copy.deepcopy(self.state)
        for fields in ({'question_submission_attempted': True}, {'question_submitted': True},
                       {'question_submitted': None}, {'question_submission_attempted': 'unknown'},
                       {'submission_receipt': {}}, {'error_type': 'TimeoutError'},
                       {'result': {'question_submission_attempted': True}}, {'started_at': 'earlier'}):
            with self.subTest(fields=fields):
                self.state = copy.deepcopy(original)
                self.state['cases']['q02'].update(fields)
                self.save_state()
                with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
                        patch('services.desktop_geo_jobs.run_test_plan') as runner:
                    self.assertIn('error', self.resume())
                    runner.assert_not_called()
        self.assertIsNone(self.jobs.worker)

    def test_wrong_cycle_path_plan_or_active_source_is_rejected_without_worker(self):
        for change in ('cycle', 'path', 'plan', 'active', 'unknown_running_case'):
            with self.subTest(change=change):
                saved_source, state = copy.deepcopy(self.source), copy.deepcopy(self.state)
                if change == 'cycle': saved_source['cycle_id'] = 'other-cycle'
                elif change == 'path': saved_source['run_dir'] = str(self.root/'outside' / ('2'*32))
                elif change == 'plan': state['plan_sha256'] = 'different'
                elif change == 'active': saved_source['status'] = 'running'
                else: state['cases']['q01']['status'] = 'running'
                self.jobs.store.save(saved_source)
                write_json(self.source_dir/'state.json', state)
                with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
                        patch('services.desktop_geo_jobs.run_test_plan') as runner:
                    self.assertIn('error', self.resume())
                    runner.assert_not_called()
        self.assertIsNone(self.jobs.worker)

    def test_current_active_native_task_blocks_resume(self):
        self.jobs.job = {'id': 'other-running', 'kind': 'run', 'status': 'running'}
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle') as cycle:
            self.assertIn('error', self.resume())
            cycle.assert_not_called()
        self.jobs.job = {'status': 'idle'}

    def test_copy_failure_never_enters_runner_and_preserves_source(self):
        before = source_manifest(self.source_dir)
        with patch('services.desktop_geo_jobs.geo_content_store.get_cycle', return_value=self.cycle), \
                patch('services.desktop_geo_jobs.copy_resume_source', side_effect=ValueError('copy not confirmed')), \
                patch('services.desktop_geo_jobs.run_test_plan') as runner:
            self.resume()
            self.jobs.worker.join(timeout=5)
            runner.assert_not_called()
        self.assertEqual(source_manifest(self.source_dir), before)
        self.assertEqual(self.jobs.job['status'], 'failed')
