import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.geo_test_reports import list_saved_reports, read_saved_report


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
        self.assertEqual(read_saved_report(self.root, reports[0]['id']), self.report)
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
            self.assertEqual(client.get('/api/geo-tests/reports/' + report_id).json['data'], self.report)
            self.assertEqual(client.get('/api/geo-tests/reports/not-a-report').status_code, 404)
            with patch.dict(app.config, ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(client.get('/api/geo-tests/reports').status_code, 401)
                self.assertEqual(client.get('/api/geo-tests/reports/' + report_id).status_code, 401)
