"""Synthetic company source maintenance; no account or external network."""
import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from services import source_maintenance_service as service
from services import geo_content_store as store
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_research_probes import ProbeFixture


class SourceFixture(ProbeFixture):
    def setUp(self):
        super().setUp()
        self.cycle = store.save_facts(self.database, self.cycle['id'], [
            {'text': '合成树科技有限公司公开介绍软件开发服务。',
             'source_url': 'https://synthetic.example.org/about', 'confirmed': True}], self.cycle['revision'])
        self.view = service.get_overview(self.database, self.cycle['id'])

    def save(self, module='website', **changes):
        item = next((item for item in self.view['modules'] if item['key'] == module), self.view['modules'][0])
        args = {'module': module, 'proposal': item['proposal'], 'expected_context_sha256': self.view['context_sha256'],
                'reference_fact_ids': item['reference_fact_ids'], **changes}
        return service.save_task(self.database, self.cycle['id'], **args)


class SourceMaintenanceTests(SourceFixture, unittest.TestCase):
    def test_company_only_two_independent_modules_and_public_factual_proposals(self):
        self.assertTrue(self.view['supported'])
        website, intro = self.save(), self.save('introduction')
        self.assertNotEqual(website['id'], intro['id'])
        self.assertEqual(website['entry_url'], 'https://help.qcc.com/spt/support/handle-dissent')
        self.assertEqual(len(service.get_overview(self.database, self.cycle['id'])['tasks']), 2)
        self.assert_original_unchanged()

    def test_product_and_unconfirmed_website_are_rejected(self):
        cycle = copy.deepcopy(self.cycle)
        cycle['profile']['entity_type'] = 'product'
        with self.assertRaises(ValueError):
            service._company(cycle)
        cycle = copy.deepcopy(self.cycle)
        cycle['facts'] = []
        with self.assertRaises(ValueError):
            service._company(cycle)

    def test_stale_context_unknown_module_fact_and_foreign_url_are_rejected(self):
        for changes in ({'expected_context_sha256': '0'*64}, {'module': 'business_registration'},
                        {'reference_fact_ids': ['unconfirmed']},
                        {'proposal': '合成树科技有限公司的官网 https://foreign.example.net'},
                        {'proposal': '合成树科技有限公司的官网 https://synthetic.example.org/unconfirmed-path'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)

    def test_reasonable_rephrasing_allowed_but_private_contact_and_invented_case_blocked(self):
        value = self.save('introduction', proposal='请核对合成树科技有限公司的软件服务介绍，来源为 https://synthetic.example.org/about 。')
        self.assertEqual(value['stage'], 'draft')
        for text in ('合成树科技有限公司联系人：13800000000',
                     '合成树科技有限公司联系 owner@synthetic.example.org',
                     '合成树科技有限公司成功交付客户案例100项。'):
            with self.assertRaises(ValueError):
                self.save('introduction', proposal=text)

    def test_draft_edit_same_id_and_write_intent_prevents_replacement(self):
        first = self.save()
        edited = self.save(proposal=first['proposal']+'请核对。', task_id=first['id'])
        self.assertEqual(first['id'], edited['id'])
        staged = service.record_state(self.database, self.cycle['id'], first['id'], stage='needs_manual_input',
            message='合成写入意图已保存。', write_attempted=True, window_open=True)
        self.assertTrue(staged['read_only'])
        with self.assertRaises(ValueError):
            self.save(proposal=first['proposal'])
        self.assertTrue(service.get_task(self.database, self.cycle['id'], first['id'])['write_attempted'])

    def test_submitted_never_downgraded_by_old_confirmation_or_closed_window(self):
        task = self.save()
        submitted = service.record_state(self.database, self.cycle['id'], task['id'], stage='submitted_pending_review',
            message='合成成功提示', write_attempted=True, confirmation_seen=True, submission_success_observed=True)
        checked = service.record_state(self.database, self.cycle['id'], task['id'], stage='submission_unknown',
            message='旧确认页仍在，只读核对。')
        self.assertEqual(checked['stage'], 'submitted_pending_review')
        self.assertTrue(checked['read_only'])
        self.assertIsNone(submitted['receipt']['public_updated'])
        self.assertIsNone(submitted['receipt']['search_adopted'])

    def test_native_pending_requires_explicit_bound_success_observation(self):
        task = self.save()
        with self.assertRaises(ValueError):
            service.record_state(self.database, self.cycle['id'], task['id'], stage='submitted_pending_review',
                message='没有成功观察的合成检查。', write_attempted=True, confirmation_seen=True)
        self.assertIsNone(service.get_task(self.database, self.cycle['id'], task['id'])['receipt'])

    def test_control_failure_preserves_inspection_only_fence_without_a_proposal_write(self):
        task = self.save()
        service.record_state(self.database, self.cycle['id'], task['id'], stage='needs_manual_review',
            message='合成控件尚未就绪。', inspection_only=True, window_open=True)
        checked = service.record_state(self.database, self.cycle['id'], task['id'], stage='window_closed',
            message='合成只读检查结束。', window_open=False)
        self.assertTrue(checked['read_only'])
        self.assertFalse(checked['write_attempted'])
        self.assertFalse(checked['can_edit'])

    def test_research_receipt_import_keeps_original_bytes_hash_and_readonly_source(self):
        item = self.view['modules'][0]
        payload = {'company': self.cycle['profile']['name'], 'platform': 'qcc', 'module': '企业官网',
            'website_requested': self.cycle['profile']['website'], 'status': 'submitted_pending_review',
            'receipt_visible': True, 'applicant_details_stored': False, 'automatic_retry': False}
        raw = json.dumps(payload).encode()
        record = service.import_research_receipt(self.database, self.cycle['id'], 'website', item['proposal'],
            self.view['context_sha256'], item['reference_fact_ids'], raw)
        self.assertEqual(record['receipt']['source_receipt_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(record['software_native_task'])
        self.assertEqual(record['source'], 'research_assisted_browser')
        self.assertTrue(record['read_only'])
        self.assertEqual(record['stage'], 'submitted_pending_review')
        self.assertEqual(raw, json.dumps(payload).encode())
        self.assert_original_unchanged()
        self.assertEqual(service.import_research_receipt(self.database, self.cycle['id'], 'website', item['proposal'],
            self.view['context_sha256'], item['reference_fact_ids'], raw)['id'], record['id'])
        original_receipt = json.dumps(record['receipt'], ensure_ascii=False)
        for stage in ('opening', 'confirmation_ready', 'submission_unknown', 'window_closed', 'submitted_pending_review'):
            checked = service.record_state(self.database, self.cycle['id'], record['id'], stage=stage,
                message='合成只读检查原申请。', window_open=stage == 'confirmation_ready')
            self.assertEqual(json.dumps(checked['receipt'], ensure_ascii=False), original_receipt)
            self.assertEqual(checked['source'], 'research_assisted_browser')
            self.assertEqual(checked['stage'], 'submitted_pending_review')

    def test_uncertain_or_wrong_company_receipt_cannot_claim_submitted(self):
        item = self.view['modules'][0]
        for payload in ({'company': '另一合成公司', 'receipt_visible': True},
                        {'company': self.cycle['profile']['name'], 'receipt_visible': False}):
            with self.assertRaises(ValueError):
                service.import_research_receipt(self.database, self.cycle['id'], 'website', item['proposal'],
                    self.view['context_sha256'], item['reference_fact_ids'], json.dumps(payload).encode())
        self.assertEqual(service.get_overview(self.database, self.cycle['id'])['tasks'], [])

    def test_same_company_other_cycle_keeps_original_task_and_blocks_duplicate(self):
        original = self.save()
        report = copy.deepcopy(self.baseline)
        report['run_id'] = 'different-synthetic-round'
        other = store.create_cycle(self.database, report, 'second-synthetic-reference')
        other = store.save_facts(self.database, other['id'], self.cycle['facts'], other['revision'])
        view = service.get_overview(self.database, other['id'])
        self.assertEqual(view['tasks'][0]['id'], original['id'])
        with self.assertRaises(ValueError):
            service.save_task(self.database, other['id'], 'website', view['modules'][0]['proposal'],
                view['context_sha256'], view['modules'][0]['reference_fact_ids'])

    def test_new_cycle_without_confirmed_facts_still_shows_original_company_task(self):
        original = self.save()
        report = copy.deepcopy(self.baseline)
        report['run_id'] = 'synthetic-round-without-facts'
        other = store.create_cycle(self.database, report, 'third-synthetic-reference')
        view = service.get_overview(self.database, other['id'])
        self.assertFalse(view['supported'])
        self.assertEqual(view['tasks'][0]['id'], original['id'])
        self.assertFalse(view['tasks'][0]['can_open'])


class SourceMaintenanceBridgeTests(SourceFixture, unittest.TestCase):
    def test_application_restart_keeps_proposal_fence_and_closes_stale_window_state(self):
        record = self.save()
        service.record_state(self.database, self.cycle['id'], record['id'], stage='confirmation_ready',
            message='合成确认页。', write_attempted=True, confirmation_seen=True, window_open=True)
        jobs = DesktopGeoJobs(self.root)
        jobs.store.save({'id': 'synthetic-interrupted-source', 'kind': 'source_maintenance', 'status': 'running',
            'cycle_id': self.cycle['id'], 'source_task_id': record['id'], 'created_at': '2026-01-03T00:00:00Z'})
        restarted = DesktopGeoJobs(self.root)
        self.addCleanup(restarted.shutdown)
        loaded = service.get_task(self.database, self.cycle['id'], record['id'])
        self.assertEqual(loaded['stage'], 'submission_unknown')
        self.assertTrue(loaded['read_only'])
        self.assertFalse(loaded['window_open'])
        self.assertEqual(restarted.get_task('synthetic-interrupted-source')['status'], 'needs_review')
        self.assert_original_unchanged()

    def test_native_window_uses_stable_profile_check_signal_and_never_final_submit(self):
        record = self.save()
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        seen = []
        async def session(task, profile, executable, *, fallback_executable_path, progress, check_requested):
            seen.append((task, profile))
            progress({'stage': 'waiting_verification', 'message': '合成验证提示', 'window_open': True})
            await asyncio.wait_for(check_requested.wait(), timeout=3)
            progress({'stage': 'submitted_pending_review', 'message': '合成明确成功提示',
                      'window_open': False, 'write_attempted': True, 'confirmation_seen': True, 'submission_success_observed': True})
            return task
        with patch('services.qcc_source_maintenance_browser.open_session', session):
            ack = jobs.open_source_maintenance(self.cycle['id'], record['id'])
            for _ in range(100):
                if jobs._source_check is not None:
                    break
                import time
                time.sleep(.01)
            self.assertIn('error', jobs.check_source_maintenance_session('not-the-task'))
            self.assertTrue(jobs.check_source_maintenance_session(ack['id'])['check_requested'])
            jobs.worker.join(timeout=5)
        self.assertFalse(jobs.worker.is_alive())
        self.assertEqual(seen[0][1], self.root / 'source-research/qcc-browser-profile')
        saved = service.get_task(self.database, self.cycle['id'], record['id'])
        self.assertEqual(saved['stage'], 'submitted_pending_review')
        self.assertFalse(saved['window_open'])
        self.assertEqual(jobs.get_task(ack['id'])['kind'], 'source_maintenance')
        self.assert_original_unchanged()


class SourceMaintenanceAPITests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, {'DATABASE_PATH': str(self.database), 'DEMO_MODE': 'false'}):
            from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.app = app
        self.client = app.test_client()
        self.endpoint = '/api/geo-content/cycles/' + self.cycle['id'] + '/source-maintenance'

    def test_api_returns_preview_and_saves_only_bound_public_task(self):
        value = self.client.get('/backend' + self.endpoint)
        self.assertEqual(value.status_code, 200)
        data = value.json['data']
        module = data['modules'][0]
        saved = self.client.post(self.endpoint, json={'module': module['key'], 'proposal': module['proposal'],
            'reference_fact_ids': module['reference_fact_ids'], 'expected_context_sha256': data['context_sha256']})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json['data']['stage'], 'draft')
        cycle = self.client.get('/api/geo-content/cycles/' + self.cycle['id']).json['data']
        self.assertEqual(cycle['source_maintenance']['tasks'][0]['id'], saved.json['data']['id'])

    def test_api_rejects_arbitrary_url_contact_js_and_keeps_access_control(self):
        for payload in ({'entry_url': 'https://elsewhere.example.org'}, {'javascript': 'anything'},
                        {'applicant_name': '合成个人'}, {'contact': '13800000000'}, []):
            self.assertEqual(self.client.post(self.endpoint, json=payload).status_code, 400)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.post(self.endpoint, json={}).status_code, 401)
