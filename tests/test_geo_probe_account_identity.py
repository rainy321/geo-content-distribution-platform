"""Native single-probe account observations; all UI and database data synthetic."""
import copy
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services import geo_content_store as store, geo_research_probe_service as probes
from services.doubao_account_identity import AccountObservedResult, bind_probe_handle, observe_visible_handle
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_runner import write_json
from tests.test_geo_research_probes import ProbeFixture, synthetic_result


class ProbeAccountIdentityTests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        baseline = copy.deepcopy(self.baseline)
        baseline['execution'].update(client_mode='consumer', session_slot='primary')
        for row in baseline['cases']:
            row.update(client_mode='consumer', session_slot='primary')
        self.cycle = store.create_cycle(self.database, baseline, 'synthetic-consumer-account-reference')
        with sqlite3.connect(self.database) as connection:
            self.frozen_baseline = connection.execute('SELECT baseline FROM geo_content_cycles WHERE id=?',
                                                     (self.cycle['id'],)).fetchone()[0]

    def observation(self, stage, *, probe_id='c' * 32, handle='user_Synthetic123', context_id='a' * 32):
        return observe_visible_handle('测试昵称 豆包号：' + handle,
            page_url='https://www.doubao.com/chat/123', settings_group_name='设置分类',
            control_role='button', visible=True, session_slot='secondary', context_id=context_id,
            profile_scope_sha256='b' * 64, stage=stage, probe_id=probe_id,
            observed_at=datetime(2026, 11, 12, 4, tzinfo=timezone.utc))

    def observed_result(self, *, probe_id='c' * 32, changed=False):
        before = self.observation('before_submission', probe_id=probe_id)
        after = self.observation('after_answer', probe_id=probe_id,
                                 handle='user_Changed123' if changed else 'user_Synthetic123')
        value = AccountObservedResult(synthetic_result(self.question,
            environment={'client_mode': 'consumer', 'model_label': '测试模型', 'session_slot': 'secondary',
                         'account_identity_status': 'unverified'}), account_observations=(before, after))
        if not changed:
            value['account_identity_evidence'] = bind_probe_handle(before, after, probe_id=probe_id,
                session_slot='secondary', context_id='a' * 32, profile_scope_sha256='b' * 64,
                source='native_collector')
            value['environment']['account_identity_status'] = 'official_visible_handle_observed'
        return value

    def build(self, value, *, run_id='c' * 32, source='native_collector'):
        return probes.build_probe(self.cycle, 'q02', value, run_id=run_id, source=source,
                                  native_task_id='d' * 32)

    def test_native_same_context_account_evidence_is_persisted_without_rewriting_baseline_or_old_probe(self):
        old = probes.attach_probe(self.database, self.cycle['id'], self.native(run_id='e' * 32,
            collector_result=synthetic_result(self.question, environment={'client_mode': 'consumer',
                'session_slot': 'secondary', 'account_identity_status': 'unverified'})), source='native_collector')
        with sqlite3.connect(self.database) as connection:
            old_raw = connection.execute('SELECT record FROM geo_research_probes WHERE id=?', (old['id'],)).fetchone()[0]
        value = self.observed_result()
        original_json = json.dumps(value, ensure_ascii=False)
        saved = probes.attach_probe(self.database, self.cycle['id'], self.native(run_id='c' * 32,
            collector_result=value), source='native_collector', native_task_id='d' * 32)
        self.assertEqual(saved['execution']['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual(saved['account_identity_evidence'], value['account_identity_evidence'])
        self.assertFalse(saved['conditions']['account_independence_verified'])
        self.assertIsNone(saved['causal_effect'])
        self.assertEqual(json.dumps(value, ensure_ascii=False), original_json)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT record FROM geo_research_probes WHERE id=?',
                                               (old['id'],)).fetchone()[0], old_raw)
            self.assertEqual(connection.execute('SELECT baseline FROM geo_content_cycles WHERE id=?',
                                               (self.cycle['id'],)).fetchone()[0], self.frozen_baseline)
            raw = connection.execute('SELECT record FROM geo_research_probes WHERE id=?', (saved['id'],)).fetchone()[0]
        self.assertNotIn('user_Synthetic123', raw)
        self.assertNotIn('测试昵称', raw)
        self.assertNotIn('account_observations', raw)
        self.assertNotIn('_native', raw)
        self.assertEqual(json.loads(raw), saved)

    def test_plain_native_dict_and_research_import_cannot_promote_serialized_identity_claims(self):
        observed = self.observed_result()
        for value, source in ((dict(observed), 'native_collector'),
                              (observed, 'research_prototype_import')):
            with self.subTest(source=source):
                saved = self.build(value, source=source)
                self.assertEqual(saved['execution']['account_identity_status'], 'unverified')
                self.assertNotIn('account_identity_evidence', saved)
                self.assertFalse(saved['conditions']['account_independence_verified'])

    def test_native_job_retains_in_memory_capability_while_verifying_serialized_result_bytes(self):
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        calls = []
        async def collect(config, *, progress):
            calls.append(config)
            self.assertEqual(config.account_probe_id, config.output_root.parent.name)
            value = self.observed_result(probe_id=config.account_probe_id)
            path = config.output_root / 'synthetic-account-result/result.json'
            write_json(path, value)
            return value, path
        with patch('services.desktop_geo_jobs.collect_doubao_sources', collect), \
                patch('services.desktop_geo_jobs.edge_executable', return_value='synthetic-edge'), \
                patch('services.desktop_geo_jobs.chrome_executable', return_value='synthetic-chrome'):
            ack = jobs.start_research_probe(self.cycle['id'], 'q02', {'session_slot': 'secondary'})
            self.assertNotIn('error', ack)
            jobs.worker.join(timeout=5)
            self.assertFalse(jobs.worker.is_alive())
        self.assertEqual(len(calls), 1)
        task = jobs.get_task(ack['id'])
        self.assertEqual(task['status'], 'completed')
        saved = task['result']['probe']
        self.assertEqual(saved['execution']['account_identity_status'], 'official_visible_handle_observed')
        self.assertEqual(saved['account_identity_evidence']['probe_id'], calls[0].account_probe_id)
        self.assertFalse(saved['conditions']['account_independence_verified'])
        raw = json.loads(Path(saved['evidence']['result_path']).read_text(encoding='utf-8'))
        self.assertNotIn('account_observations', raw)
        self.assertEqual(raw['account_identity_evidence'], saved['account_identity_evidence'])

    def test_real_research_import_path_does_not_reconstruct_native_account_capability(self):
        value = dict(self.observed_result())
        value['environment']['session_slot'] = 'primary'
        payload = self.prototype()
        payload['collector_result'] = value
        payload['execution'].update(client_mode='consumer', session_slot='primary')
        saved = probes.normalize_import(self.cycle, json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(saved['execution']['account_identity_status'], 'unverified')
        self.assertNotIn('account_identity_evidence', saved)
        self.assertFalse(saved['conditions']['account_independence_verified'])

    def test_tampered_or_wrong_probe_safe_evidence_is_not_accepted(self):
        value = self.observed_result()
        value['account_identity_evidence']['observations'][1]['context_id'] = 'f' * 32
        self.assertEqual(self.build(value)['execution']['account_identity_status'], 'unverified')
        self.assertEqual(self.build(self.observed_result(), run_id='f' * 32)['execution']['account_identity_status'],
                         'unverified')
        work = self.observed_result()
        work['environment']['client_mode'] = 'work'
        # Work/secondary is an invalid execution scope rather than an account proof.
        with self.assertRaises(ValueError):
            self.build(work)

    def test_same_context_handle_change_is_explained_but_remains_unverified(self):
        saved = self.build(self.observed_result(changed=True))
        self.assertEqual(saved['execution']['account_identity_status'], 'unverified')
        self.assertEqual(saved['execution']['account_identity_reason'], 'visible_handle_changed_during_probe')
        self.assertNotIn('account_identity_evidence', saved)
        self.assertFalse(saved['conditions']['account_independence_verified'])
        forged = dict(self.observed_result(changed=True))
        forged['environment']['account_identity_reason'] = 'visible_handle_changed_during_probe'
        self.assertNotIn('account_identity_reason', self.build(forged)['execution'])

    def test_legacy_probe_without_account_observations_retains_original_unverified_shape(self):
        saved = self.build(synthetic_result(self.question, environment={'client_mode': 'consumer',
            'session_slot': 'primary'}))
        self.assertEqual(saved['execution']['account_identity_status'], 'unverified')
        self.assertNotIn('account_identity_reason', saved['execution'])
        self.assertNotIn('account_identity_evidence', saved)
        self.assertFalse(saved['conditions']['account_independence_verified'])


if __name__ == '__main__':
    unittest.main()
