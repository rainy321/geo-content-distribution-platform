import json
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.geo_test_reports import list_saved_reports, read_saved_report, project_report_evaluation


class TestSavedReports(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'reports'
        run = self.root / 'synthetic-client' / 'run-001'
        run.mkdir(parents=True)
        self.report = {'schema_version': '1.0', 'kind': 'geo_search_test_report',
                       'profile': {'name': '合成报告验收客户'}, 'cases': [],
                       'status': 'completed', 'updated_at': '2026-10-13T00:00:00Z'}
        (run / 'report.json').write_text(json.dumps(self.report), encoding='utf-8')

    def test_library_returns_report_without_accepting_paths(self):
        reports = list_saved_reports(self.root)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['name'], '合成报告验收客户')
        self.assertNotIn('path', reports[0])
        projected = read_saved_report(self.root, reports[0]['id'])
        for field, value in self.report.items():
            self.assertEqual(projected[field], value)
        self.assertTrue(projected['evaluation_projection']['source_file_unchanged'])
        self.assertFalse(projected['evaluation_projection']['new_collection_performed'])
        self.assertIsNone(read_saved_report(self.root, '../secret.json'))

    def test_invalid_json_and_non_reports_are_skipped(self):
        for name, content in [('invalid', '{'), ('plan', '{"schema_version":"1.0"}'), ('array', '[]')]:
            path = self.root / name / 'report.json'
            path.parent.mkdir()
            path.write_text(content, encoding='utf-8')
        self.assertEqual(len(list_saved_reports(self.root)), 1)
        self.assertEqual(list_saved_reports(self.root / 'missing'), [])

    def test_readonly_api_and_existing_access_control(self):
        from sau_backend import app
        with patch.dict(app.config, GEO_TEST_REPORTS_DIRECTORY=self.root, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            client = app.test_client()
            response = client.get('/api/geo-tests/reports')
            self.assertEqual(response.status_code, 200)
            report_id = response.json['data'][0]['id']
            projected = client.get('/api/geo-tests/reports/' + report_id).json['data']
            for field, value in self.report.items():
                self.assertEqual(projected[field], value)
            self.assertIn('只读评估投影', projected['evaluation_projection']['note'])
            self.assertEqual(client.get('/api/geo-tests/reports/not-a-report').status_code, 404)
            with patch.dict(app.config, ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(client.get('/api/geo-tests/reports').status_code, 401)
                self.assertEqual(client.get('/api/geo-tests/reports/' + report_id).status_code, 401)

    def _legacy_background_report(self):
        from services.geo_test_service import report_cohorts
        assessment = {'answer_evaluable': True, 'answer_error': None, 'brand_mentioned': False,
                      'official_source_present': None, 'official_citation_present': None,
                      'matched_terms': [], 'mention_evidence': [], 'recommended': None, 'fact_accuracy': None}
        cases = [
            {'id': 'q01', 'category': 'discovery', 'question': '有哪些可核验的方案？', 'intent': '选型',
             'status': 'success', 'response': '已把这个调研任务委托给专门的调研代理执行了。调研在后台进行，完成后我会把候选清单整理给你。',
             'citations': [], 'displayed_sources': [], 'result_path': 'evidence/q01/result.json',
             'collected_at': '2026-10-20T00:00:00+08:00', 'model_label': '合成工作模型', 'client_mode': 'work',
             'assessment': copy.deepcopy(assessment), 'raw_result': {'retained_original_evidence': True}},
            {'id': 'q02', 'category': 'brand_identity', 'question': '品牌由谁运营？', 'intent': '核验',
             'status': 'failed', 'response': '懂AI中转站', 'failure_reason': 'synthetic_missing_submission',
             'assessment': {**assessment, 'answer_evaluable': False, 'brand_mentioned': None}},
            {'id': 'q03', 'category': 'website_guided', 'question': '请查看官网', 'intent': '核验',
             'status': 'pending', 'response': None,
             'assessment': {**assessment, 'answer_evaluable': False, 'brand_mentioned': None}},
        ]
        return {**self.report, 'evaluation_version': '1.1', 'run_id': 'synthetic-legacy-background',
                'status': 'stopped', 'stop_reason': 'synthetic_stop_preserved', 'plan_sha256': 'plan-preserved',
                'profile': {'name': '懂AI中转站', 'website': 'https://tongyuye.com'},
                'planner': {'evidence_path': 'planner-evidence-preserved'},
                'execution': {'client_mode': 'work', 'requested_model_label': '合成工作模型'},
                'cohorts': report_cohorts(cases), 'cases': cases}

    def test_read_projection_excludes_background_placeholder_without_rewriting_evidence(self):
        legacy = self._legacy_background_report()
        snapshot = copy.deepcopy(legacy)
        projected = project_report_evaluation(legacy)
        self.assertEqual(legacy, snapshot)
        self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])
        self.assertIsNone(projected['cases'][0]['assessment']['brand_mentioned'])
        self.assertEqual(projected['cases'][0]['assessment']['answer_error'], 'background_task_not_final')
        self.assertEqual(projected['cases'][0]['status'], 'deferred_answer')
        self.assertEqual(projected['cohorts']['discovery']['answers_evaluable'], 0)
        self.assertIsNone(projected['cohorts']['discovery']['brand_mention_rate'])
        self.assertEqual(projected['status'], 'stopped')
        for field in ('run_id', 'updated_at', 'stop_reason', 'plan_sha256', 'planner', 'profile', 'execution'):
            self.assertEqual(projected[field], legacy[field])
        for before, after in zip(legacy['cases'], projected['cases']):
            for field, value in before.items():
                if field not in ('assessment', 'status'):
                    self.assertEqual(after[field], value)
        self.assertEqual(projected['cases'][1]['status'], 'failed')
        self.assertFalse(projected['cases'][1]['assessment']['answer_evaluable'])

    def test_projection_never_promotes_missing_or_failed_collection_evidence(self):
        legacy = self._legacy_background_report()
        legacy['cases'][0]['response'] = '懂AI中转站有公开资料。'
        legacy['cases'][0]['assessment']['answer_evaluable'] = False
        projected = project_report_evaluation(legacy)
        self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])
        self.assertEqual(projected['cohorts']['discovery']['answers_evaluable'], 0)
        legacy['cases'][0]['status'] = 'failed'
        legacy['cases'][0]['assessment']['answer_evaluable'] = True
        projected = project_report_evaluation(legacy)
        self.assertEqual(projected['cases'][0]['status'], 'failed')
        self.assertFalse(projected['cases'][0]['assessment']['answer_evaluable'])

    def test_completed_projection_can_downgrade_without_upgrading_a_failed_report(self):
        legacy = self._legacy_background_report()
        legacy['status'] = 'completed'
        self.assertEqual(project_report_evaluation(legacy)['status'], 'completed_with_gaps')
        legacy['status'] = 'failed'
        self.assertEqual(project_report_evaluation(legacy)['status'], 'failed')

    def test_final_answer_keeps_existing_metrics_and_unknown_source_state(self):
        legacy = self._legacy_background_report()
        legacy['cases'][0]['response'] = ('调研已完成。前面曾委托专门的调研代理执行，调研在后台进行。'
                                          '以下是最终结果：方案甲提供统一模型网关，来源如下。')
        projected = project_report_evaluation(legacy)
        self.assertTrue(projected['cases'][0]['assessment']['answer_evaluable'])
        self.assertEqual(projected['cases'][0]['status'], 'success')
        self.assertIsNone(projected['cases'][0]['assessment']['official_source_present'])
        self.assertIsNone(projected['cases'][0]['assessment']['official_citation_present'])
        self.assertEqual(projected['cohorts'], legacy['cohorts'])

    def test_capacity_placeholder_also_downgrades_legacy_success(self):
        legacy = self._legacy_background_report()
        legacy['cases'][0]['response'] = '算力紧张，办公能力得晚点才能用了。开通订阅，享高峰期优先使用。'
        projected = project_report_evaluation(legacy)
        self.assertEqual(projected['cases'][0]['status'], 'service_unavailable')
        self.assertEqual(projected['cases'][0]['assessment']['answer_error'], 'service_capacity_unavailable')
        self.assertIsNone(projected['cohorts']['discovery']['brand_mention_rate'])

    def test_report_api_uses_current_rules_and_keeps_file_bytes_and_timestamp(self):
        from sau_backend import app
        legacy = self._legacy_background_report()
        path = self.root / 'synthetic-client/run-001/report.json'
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        with patch.dict(app.config, GEO_TEST_REPORTS_DIRECTORY=self.root, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            client = app.test_client()
            listing = client.get('/api/geo-tests/reports').json['data'][0]
            report_id = listing['id']
            first = client.get('/api/geo-tests/reports/' + report_id).json['data']
            second = client.get('/api/geo-tests/reports/' + report_id).json['data']
        self.assertEqual(first, second)
        self.assertEqual(first['cohorts']['discovery']['answers_evaluable'], 0)
        self.assertIsNone(first['cohorts']['discovery']['brand_mention_rate'])
        self.assertEqual(first['evaluation_projection']['stored_evaluation_version'], '1.1')
        self.assertEqual(first['evaluation_projection']['changed_case_ids'], ['q01'])
        self.assertIn('q01', listing['evaluation_projection']['changed_case_ids'])
        self.assertEqual(before, (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns))
