"""Normal local HTTP contract against an isolated database, never a platform POST."""
import json
import os
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from services import geo_destination_service as destinations, geo_directory_service as directory
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_destinations import config
from tests.test_geo_directory_service import DirectoryFixture


class DirectoryApiTests(DirectoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Importing the Flask module initializes its configured DB and media
        # directories; pin every path to this synthetic fixture before import.
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), MEDIA_ROOT=str(self.root/'media'),
                        COOKIES_DIRECTORY=str(self.root/'cookies'), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        ALLOW_REAL_PUBLISHING='false', ENABLE_PUBLISH_SCHEDULER='false'):
            from sau_backend import app
        self.app = app
        self.settings = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.client = app.test_client()
        self.endpoint = '/api/geo-content/cycles/' + self.cycle_id + '/directory-applications'

    def test_apiznz_receipt_is_reviewed_external_only_and_evidence_cannot_cross_projects(self):
        target_config = config(name='API指南针', home_url='https://www.apiznz.com/',
            source_scope='https://www.apiznz.com/', editor_url=directory.APIZNZ_ENTRY_URL,
            login_url=directory.APIZNZ_ENTRY_URL, title_selector='', body_selector='', publication_use='commercial')
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)

        def draft(cycle):
            target = destinations.save_target(self.database, cycle['project_id'], target_config)
            endpoint = '/api/geo-content/cycles/' + cycle['id'] + '/directory-applications'
            view = self.client.get(endpoint, query_string={'target_id': target['id']}).json['data']
            self.assertTrue(view['supported'])
            self.assertEqual(view['adapter'], directory.APIZNZ_ADAPTER)
            name, website = cycle['profile']['name'], cycle['profile']['website']
            fields = {'name': name, 'domain': website.split('/')[2], 'intro': f'{name} 官方资料 {website}',
                'api_base': website.rstrip('/') + '/v1'}
            request = {'target_id': target['id'], 'expected_context_sha256': view['context_sha256'], 'fields': fields}
            for changes in ({'contact': 'example@example.org'}, {'intro': 'example@example.org'},
                            {'domain': 'https://different.example'}, {'api_base': 'https://different.example/v1'}):
                self.assertEqual(self.client.post(endpoint, json={**request, 'fields': {**fields, **changes}}).status_code, 400)
            response = self.client.post(endpoint, json=request)
            self.assertEqual(response.status_code, 200, response.json)
            record = response.json['data']
            return endpoint + '/' + record['id'], record

        def confirmation(record):
            return {'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'], 'confirmed': True}

        receipt = {'status': 'submitted_pending_review', 'observed_at': '2026-01-01T10:15+08:00',
            'observed_at_precision': 'minute', 'note': '合成测试：平台显示提交成功', 'evidence_sha256': 'a' * 64}
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No external requests')):
            grok = self.save()
            endpoint, record = draft(self.cycle)
            self.assertFalse(record['can_record_external_receipt'])
            self.assertEqual(self.client.post(endpoint+'/external-receipt', json={**confirmation(record), **receipt}).status_code, 400)
            reviewed = self.client.post(endpoint+'/review', json=confirmation(record)).json['data']
            self.assertTrue(reviewed['can_record_external_receipt'])
            self.assertFalse(reviewed['can_open'])
            self.assertFalse(reviewed['can_inspect'])
            self.assertIsNone(reviewed['open_nonce'])
            self.assertIn('error', jobs.inspect_directory_application(self.cycle_id, record['id']))
            self.assertIn('error', jobs.open_directory_application(self.cycle_id, record['id'], record['review_sha256'], 'fake'))
            self.assertIsNone(jobs.worker)
            request = {**confirmation(reviewed), **receipt}
            for changes in ({'status': 'accepted'}, {'intake_uid': 'lead-fabricated'}, {'evidence_sha256': None}):
                self.assertEqual(self.client.post(endpoint+'/external-receipt', json={**request, **changes}).status_code, 400)
            response = self.client.post(endpoint+'/external-receipt', json=request)
            self.assertEqual(response.status_code, 200, response.json)
            pending = response.json['data']
            self.assertEqual(self.client.get(endpoint).json['data'], pending)
            self.assertEqual(pending['stage'], 'submitted_pending_review')
            self.assertEqual(pending['evidence_level'], 'owner_reported')
            self.assertEqual(pending['receipt']['evidence_sha256'], receipt['evidence_sha256'])
            self.assertIsNone(pending['receipt']['intake_uid'])
            self.assertFalse(pending['receipt']['public_listing_verified'])
            self.assertFalse(pending['geo_effect_evidence'])
            self.assertFalse(pending['native_submit_attempted'])
            self.assertEqual(self.client.post(endpoint+'/external-receipt', json=request).status_code, 400)
            self.assertEqual(directory.get_application(self.database, self.cycle_id, grok['id']), grok)

            other_endpoint, other = draft(self.other_cycle('另一合成品牌'))
            other_reviewed = self.client.post(other_endpoint+'/review', json=confirmation(other)).json['data']
            duplicate = self.client.post(other_endpoint+'/external-receipt', json={**confirmation(other_reviewed), **receipt})
            self.assertEqual(duplicate.status_code, 400)
            self.assertIn('另一份申请', duplicate.json['msg'])

    def test_normal_create_review_external_receipt_uses_only_new_table_and_no_platform(self):
        with closing(sqlite3.connect(self.database)) as c:
            tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {name: c.execute('SELECT * FROM "' + name.replace('"','""') + '" ORDER BY rowid').fetchall() for name in tables}
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No external requests')):
            view = self.client.get(self.endpoint, query_string={'target_id': self.target['id']})
            self.assertEqual(view.status_code, 200)
            saved = self.client.post(self.endpoint, json={'target_id': self.target['id'],
                'expected_context_sha256': view.json['data']['context_sha256'], 'fields': self.fields})
            self.assertEqual(saved.status_code, 200, saved.json)
            record = saved.json['data']
            self.assertEqual(self.client.get(self.endpoint+'/'+record['id']).json['data'], record)
            reviewed = self.client.post(self.endpoint+'/'+record['id']+'/review', json={
                'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'], 'confirmed': True})
            self.assertEqual(reviewed.status_code, 200)
            record = reviewed.json['data']
            receipt = self.client.post(self.endpoint+'/'+record['id']+'/external-receipt', json={
                'expected_revision': record['revision'], 'expected_review_sha256': record['review_sha256'], 'confirmed': True,
                'status': 'submitted_pending_review', 'intake_uid': 'lead-api-fixture-01',
                'observed_at': '2026-01-01T10:15+08:00', 'observed_at_precision': 'minute', 'note': '合成本人回执，待平台审核'})
            self.assertEqual(receipt.status_code, 200, receipt.json)
            self.assertEqual(receipt.json['data']['evidence_level'], 'owner_reported')
            self.assertEqual(receipt.json['data']['source'], 'operator_assisted')
            self.assertFalse(receipt.json['data']['can_open'])
        with closing(sqlite3.connect(self.database)) as c:
            for name, rows in before.items():
                self.assertEqual(c.execute('SELECT * FROM "' + name.replace('"','""') + '" ORDER BY rowid').fetchall(), rows, name)

    def test_unknown_action_origin_duplicate_keys_forms_and_scope_overrides_are_rejected(self):
        record = self.save()
        for suffix in ('submit', 'retry', 'clear-lock'):
            self.assertEqual(self.client.post(self.endpoint+'/'+record['id']+'/'+suffix, json={}).status_code, 400)
        self.assertEqual(self.client.post(self.endpoint, json={}, headers={'Origin': 'https://unrelated.example'}).status_code, 403)
        self.assertEqual(self.client.post(self.endpoint, data={'fields': 'not JSON'}).status_code, 415)
        self.assertEqual(self.client.post(self.endpoint, data='{"fields":{},"fields":{}}', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(self.endpoint, query_string=[('target_id', self.target['id']), ('target_id', self.target['id'])]).status_code, 400)
        self.assertEqual(self.client.get(self.endpoint+'/'+record['id'], query_string={'cycle_id': 'other'}).status_code, 400)
        self.assertEqual(self.client.post(self.endpoint, data='"'+'x'*17000+'"', content_type='application/json').status_code, 413)
        self.assertEqual(directory.get_application(self.database, self.cycle_id, record['id']), record)


if __name__ == '__main__':
    unittest.main()
