"""First external baseline from an empty customer DB; no browsers or real data."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_external_baseline as external, geo_content_store as store
from services import geo_content_research as research, geo_research_probe_service as probes
from services.geo_external_baseline_contract import question_sha256
from services.project_service import create_project, delete_project, ProjectHasArticlesError
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_content_loop import facts, draft, report


class ExternalBaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.database = initialize_database(self.root / 'database.db')
        self.project = create_project(self.database, name='合成外部云科技有限公司', website='https://external.example.org/',
            product='企业AI应用与系统集成', industry='企业软件服务', description='合成客户测试资料，非真实业务事实。',
            keywords=['AI应用'], competitors=[], entity_type='company', company='合成外部云科技有限公司')
        self.project_id = self.project['id']
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root/'media'), COOKIES_DIRECTORY=str(self.root/'cookies')):
            from sau_backend import app
        config = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        config.start(); self.addCleanup(config.stop)
        self.app = app; self.client = app.test_client()
        self.prepare_url = '/api/geo-content/external-baseline-plans/prepare'
        self.freeze_url = '/api/geo-content/external-baseline-plans/freeze'
        self.create_url = '/api/geo-content/external-cycles'
        self.pending_url = '/api/geo-content/external-baseline-plans'
        self.raw = '  用户手机提供的合成回答。\r\n关于工商或处罚的说法只是回答原文，不能当作客户事实。\n\n'

    def post(self, path, payload, status=200):
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()['data']

    def prepare(self):
        return self.post(self.prepare_url, {'project_id': self.project_id})

    def freeze_payload(self, prepared=None, **changes):
        prepared = prepared or self.prepare()
        return {'request_id': 'freeze-request-0001', 'project_id': self.project_id,
                'expected_profile_sha256': prepared['profile_sha256'], 'expected_plan_sha256': prepared['plan_sha256'],
                'plan': prepared['plan'], 'confirmed': True, **changes}

    def frozen(self):
        return self.post(self.freeze_url, self.freeze_payload())['frozen_plan']

    def create_payload(self, frozen=None, **changes):
        frozen = frozen or self.frozen(); case = frozen['plan']['cases'][0]
        return {'request_id': 'create-request-0001', 'project_id': self.project_id, 'frozen_plan_id': frozen['id'],
                'expected_profile_sha256': frozen['profile_sha256'], 'expected_plan_sha256': frozen['plan_sha256'],
                'first_observation': {'case_id': case['id'], 'expected_question_sha256': question_sha256(case['question']),
                                      'raw_response_text': self.raw}, **changes}

    def create(self):
        return self.post(self.create_url, self.create_payload())

    def counts(self):
        with sqlite3.connect(self.database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {table: connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] if table in tables else 0
                    for table in ('geo_content_cycles','geo_research_probes','geo_external_baseline_plans','geo_external_baseline_requests','articles')}

    def test_prepare_is_read_only_template_and_has_no_sampling_or_fake_report(self):
        before = self.database.read_bytes()
        prepared = self.prepare()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(prepared['plan']['planner']['method'], 'template_not_ai')
        self.assertTrue(prepared['requires_user_confirmation'])
        self.assertEqual(len(prepared['plan']['cases']), 3)
        self.assertNotIn('conditions', prepared); self.assertNotIn('report', prepared)
        self.assertEqual(self.counts()['geo_content_cycles'], 0)

    def test_freeze_is_immutable_explicitly_nonAI_and_exact_retry_does_not_write(self):
        payload = self.freeze_payload(); first = self.post(self.freeze_url, payload)
        self.assertEqual(first['frozen_plan']['plan']['planner']['method'], 'template_not_ai')
        before = self.database.read_bytes(); second = self.post(self.freeze_url, payload)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(first['frozen_plan'], second['frozen_plan']); self.assertTrue(second['receipt']['idempotent'])
        changed = copy.deepcopy(payload); changed['confirmed'] = False
        self.post(self.freeze_url, changed, 409)
        changed = copy.deepcopy(payload); changed['plan']['planner']['note'] = 'different metadata'
        self.post(self.freeze_url, changed, 409)
        self.assertEqual(self.counts()['geo_external_baseline_plans'], 1)

    def test_existing_plan_with_null_planner_does_not_generate_or_depend_on_templates(self):
        payload = self.freeze_payload(); payload['plan']['planner'] = None
        original = external.prepare_plan
        def only_existing(profile, existing_plan=None):
            self.assertIsNotNone(existing_plan, 'Existing plan freeze must not generate a template')
            return original(profile, existing_plan)
        with patch.object(external, 'prepare_plan', side_effect=only_existing):
            result = self.post(self.freeze_url, payload)
        self.assertEqual(result['frozen_plan']['plan']['cases'], payload['plan']['cases'])
        self.assertEqual(result['frozen_plan']['plan']['planner']['method'], 'user_provided_not_verified')

    def test_freeze_nonobject_plan_is_400_with_zero_writes(self):
        payload = self.freeze_payload(); before = self.counts(); original_bytes = self.database.read_bytes()
        for invalid in (None, [], 'not a frozen plan'):
            with self.subTest(plan_type=type(invalid).__name__):
                self.post(self.freeze_url, {**payload, 'plan': invalid}, 400)
                self.assertEqual(self.counts(), before)
                self.assertEqual(self.database.read_bytes(), original_bytes)

    def test_freeze_requires_confirmation_and_keeps_project_history_deletion_readable(self):
        payload = self.freeze_payload(confirmed=False)
        self.post(self.freeze_url, payload, 400)
        payload['confirmed'] = True; self.post(self.freeze_url, payload)
        with self.assertRaises(ProjectHasArticlesError): delete_project(self.database, self.project_id)
        response = self.client.delete('/api/projects/' + str(self.project_id))
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.counts()['geo_external_baseline_plans'], 1)

    def test_frozen_plan_survives_reopen_without_answers_or_cross_project_selection(self):
        frozen = self.frozen()
        other = create_project(self.database, name='另一个合成品牌', website='https://other.example.org/',
            product='合成产品', industry='合成行业', description='', keywords=[], competitors=[])
        before = self.database.read_bytes()
        reopened = self.app.test_client()
        response = reopened.get(self.pending_url, query_string={'project_id': self.project_id})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = response.get_json()['data']
        self.assertFalse(data['has_more']); self.assertIsNone(data['next_offset'])
        self.assertEqual(len(data['plans']), 1)
        recovered = data['plans'][0]
        self.assertTrue(recovered['created_at']); self.assertTrue(recovered['current_profile_matches'])
        self.assertEqual({key: recovered[key] for key in frozen}, frozen)
        self.assertNotIn('raw_response_text', json.dumps(data)); self.assertNotIn('observations', data['plans'][0])
        other_data = reopened.get(self.pending_url, query_string={'project_id': other['id']}).get_json()['data']
        self.assertEqual(other_data, {'plans': [], 'has_more': False, 'next_offset': None})
        self.assertEqual(self.database.read_bytes(), before)
        created = self.post(self.create_url, self.create_payload(recovered))
        self.assertEqual(created['cycle']['external_baseline']['frozen_plan_id'], frozen['id'])
        remaining = reopened.get(self.pending_url, query_string={'project_id': self.project_id}).get_json()['data']
        self.assertEqual(remaining['plans'], [])

    def test_frozen_plan_history_remains_readable_after_profile_change_but_create_conflicts(self):
        frozen = self.frozen()
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE projects SET website=? WHERE id=?', ('https://changed.example.org/', self.project_id))
        before = self.database.read_bytes(); counts = self.counts()
        response = self.client.get(self.pending_url, query_string={'project_id': self.project_id})
        self.assertEqual(response.status_code, 200)
        recovered = response.get_json()['data']['plans'][0]
        self.assertFalse(recovered['current_profile_matches'])
        self.assertEqual(recovered['profile'], frozen['profile']); self.assertEqual(recovered['plan'], frozen['plan'])
        self.assertEqual(self.database.read_bytes(), before)
        self.post(self.create_url, self.create_payload(recovered), 409)
        self.assertEqual(self.counts(), counts)

    def test_pending_plans_are_bounded_paginated_and_read_only(self):
        payload = self.freeze_payload()
        for index in range(23):
            self.post(self.freeze_url, {**payload, 'request_id': 'freeze-page-request-%02d' % index})
        before = self.database.read_bytes()
        first = self.client.get(self.pending_url, query_string={'project_id': self.project_id}).get_json()['data']
        second = self.client.get(self.pending_url, query_string={'project_id': self.project_id, 'offset': first['next_offset']}).get_json()['data']
        self.assertEqual(len(first['plans']), 20); self.assertTrue(first['has_more']); self.assertEqual(first['next_offset'], 20)
        self.assertEqual(len(second['plans']), 3); self.assertFalse(second['has_more']); self.assertIsNone(second['next_offset'])
        all_ids = [row['id'] for row in first['plans'] + second['plans']]
        self.assertEqual(len(set(all_ids)), 23)
        ordering = [(row['created_at'], row['id']) for row in first['plans'] + second['plans']]
        self.assertEqual(ordering, sorted(ordering, reverse=True))
        self.assertEqual(self.database.read_bytes(), before)

    def test_pending_plan_read_requires_explicit_valid_scope_and_original_access_guards(self):
        self.frozen(); before = self.database.read_bytes()
        for query in ({}, {'project_id': 'true'}, {'project_id': 0}, {'project_id': self.project_id, 'offset': -1},
                      {'project_id': self.project_id, 'offset': 1000001}, {'project_id': self.project_id, 'answer': 'x'},
                      [('project_id', str(self.project_id)), ('project_id', str(self.project_id))]):
            response = self.client.get(self.pending_url, query_string=query)
            self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.client.get(self.pending_url, query_string={'project_id': 999999}).status_code, 404)
        self.assertEqual(self.client.get(self.pending_url, query_string={'project_id': self.project_id},
            headers={'Origin': 'https://foreign.example.org'}).status_code, 403)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.get(self.pending_url, query_string={'project_id': self.project_id}).status_code, 401)
        self.assertEqual(self.database.read_bytes(), before)

    def test_atomic_first_record_from_no_report_preserves_raw_and_unknowns(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
            result = self.create()
        cycle, probe = result['cycle'], result['probe']
        self.assertIsNone(cycle['baseline']); self.assertEqual(cycle['external_baseline']['kind'], external.KIND)
        self.assertEqual(cycle['baseline_source'], 'user_reported_external')
        self.assertFalse(cycle['capabilities']['native_metrics']); self.assertFalse(cycle['capabilities']['native_probe'])
        self.assertTrue(cycle['capabilities']['work_drafting'])
        self.assertEqual(probe['after']['response'], self.raw)
        self.assertEqual(probe['response_sha256'], hashlib.sha256(self.raw.encode()).hexdigest())
        self.assertEqual(probe['observation_role'], 'baseline'); self.assertFalse(probe['native_verified'])
        self.assertIsNone(probe['after']['answer_evaluable']); self.assertIsNone(probe['after']['model_label'])
        self.assertTrue(all(value is None for value in probe['conditions'].values()))
        self.assertIsNone(probe['execution']); self.assertIsNone(probe['evidence'])
        self.assertEqual(cycle['facts'], []); self.assertEqual(cycle['observations']['sources'], [])
        self.assertEqual(self.counts()['geo_content_cycles'], 1); self.assertEqual(self.counts()['geo_research_probes'], 1)
        self.assertEqual(cycle['external_baseline']['baseline_observation_ids'], [probe['id']])
        self.assertNotIn('geo_search_test_report', json.dumps(cycle['external_baseline']))
        listed = store.list_cycles(self.database)[0]
        self.assertEqual(listed['baseline_kind'], external.KIND); self.assertFalse(listed['capabilities']['native_metrics'])

    def test_duplicate_create_returns_same_cycle_and_changed_payload_conflicts(self):
        payload = self.create_payload(); first = self.post(self.create_url, payload)
        before = self.database.read_bytes(); again = self.post(self.create_url, payload)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(first['cycle']['id'], again['cycle']['id']); self.assertTrue(again['receipt']['idempotent'])
        changed = copy.deepcopy(payload); changed['first_observation']['raw_response_text'] += '编辑后的内容'
        self.post(self.create_url, changed, 409)
        self.assertEqual(self.counts()['geo_content_cycles'], 1); self.assertEqual(self.counts()['geo_research_probes'], 1)

    def test_first_text_size_empty_paths_and_forgery_roll_back_every_cycle_write(self):
        base = self.create_payload(); before = self.counts()
        invalid = ['', '  ', None, '/private/answer.txt', 'file:///C:/private.txt', 'x'*(1024*1024+1)]
        for raw in invalid:
            with self.subTest(type=type(raw).__name__):
                payload = copy.deepcopy(base); payload['first_observation']['raw_response_text'] = raw
                self.post(self.create_url, payload, 400); self.assertEqual(self.counts(), before)
        for field in ('native_verified','evidence','conditions','business_relationship_review','question_with_url'):
            payload = copy.deepcopy(base); payload['first_observation']['metadata'] = {field: True}
            self.post(self.create_url, payload, 400); self.assertEqual(self.counts(), before)

    def test_exact_one_MiB_is_preserved_and_json_body_limit_is_separate(self):
        payload = self.create_payload(); payload['first_observation']['raw_response_text'] = 'x'*(1024*1024)
        result = self.post(self.create_url, payload)
        self.assertEqual(len(result['probe']['after']['response']), 1024*1024)
        self.assertEqual(self.client.post(self.create_url, data=b' '*(1100*1024+1), content_type='application/json').status_code, 413)

    def test_project_profile_plan_question_and_case_CAS_reject_cross_scope(self):
        base = self.create_payload(); before = self.counts()
        for change in ({'expected_profile_sha256':'0'*64}, {'expected_plan_sha256':'0'*64}, {'project_id': True}):
            self.post(self.create_url, {**base, **change}, 400 if change.get('project_id') is True else 409)
        for change in ({'case_id':'q99'}, {'expected_question_sha256':'0'*64}):
            payload = copy.deepcopy(base); payload['first_observation'].update(change)
            self.post(self.create_url, payload, 409)
        self.assertEqual(self.counts(), before)
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE projects SET website=? WHERE id=?', ('https://different.example.org/', self.project_id))
        self.post(self.create_url, base, 409); self.assertEqual(self.counts(), before)

    def test_frozen_question_markers_cannot_be_overwritten_by_observed_metadata(self):
        frozen = self.frozen(); case = frozen['plan']['cases'][0]
        self.assertFalse(frozen['case_markers'][case['id']]['question_with_url'])
        payload = self.create_payload(frozen); payload['first_observation']['metadata'] = {
            'question':'用户声明自己实际问了另一个 https://other.example.org/ 问题', 'answer_complete':True,
            'source_links':[{'url':'https://sources.example.org/page','title':'工商线索'}]}
        result = self.post(self.create_url, payload); probe = result['probe']
        self.assertFalse(probe['question_with_url']); self.assertEqual(probe['question'], case['question'])
        self.assertFalse(probe['conditions']['same_question']); self.assertFalse(probe['native_verified'])
        self.assertTrue(probe['after']['answer_evaluable']); self.assertEqual(probe['after']['completeness_basis'], 'user_statement')
        self.assertFalse(probe['external_submission']['independent_verification'])
        self.assertEqual(probe['external_source_links'][0]['url'], 'https://sources.example.org/page')
        self.assertFalse(probe['external_source_links'][0]['verified']); self.assertEqual(result['cycle']['facts'], [])
        self.assertFalse(result['cycle']['observations']['cases'][0]['usable'])

    def test_real_registered_bare_domain_full_url_and_unprompted_question_markers(self):
        profile = {'website': 'https://tongyuye.com/', 'official_urls': ['https://api.tongyuye.com/']}
        self.assertTrue(external.question_has_url('请读 tongyuye.com 回答北京同于野的业务。', profile))
        self.assertTrue(external.question_has_url('请查看 https://tongyuye.com/ 的介绍。', profile))
        self.assertTrue(external.question_has_url('请看 api.tongyuye.com 的公开说明。', profile))
        self.assertFalse(external.question_has_url('有哪些企业AI开发服务商可供比较？', profile))

    def test_normal_followup_and_explicit_context_still_work_without_native_context(self):
        created = self.create(); cycle = created['cycle']; case = cycle['test_plan']['cases'][1]
        url = '/api/geo-content/cycles/' + cycle['id'] + '/external-observations'
        body = {'project_id':self.project_id,'expected_revision':cycle['revision'],'expected_plan_sha256':cycle['test_plan']['plan_sha256'],
                'case_id':case['id'],'raw_response_text':'另一个外部观察，尚未核验。'}
        record = self.post(url, body)['probe']; self.assertIsNone(record['before']['answer_evaluable'])
        context = {key:body[key] for key in ('project_id','expected_revision','expected_plan_sha256','case_id')}
        context.update(expected_response_sha256=record['response_sha256'],expected_context_sequence=0,
            attestation={'question':case['question'],'new_conversation':True,'first_message':True,'confirmation_text':'用户明确声明，非独立核验。'})
        amended = self.post(url+'/'+record['id']+'/context', context)['probe']
        self.assertFalse(amended['native_verified']); self.assertFalse(amended['context_attestations'][0]['independent_verification'])
        self.assertTrue(amended['conditions']['same_question'])

    def test_native_entrypoints_reject_before_starting_and_report_guard_is_unchanged(self):
        cycle = self.create()['cycle']; jobs = DesktopGeoJobs(self.root)
        with patch.object(jobs, '_start') as started, patch.object(jobs, '_config') as config:
            self.assertIn('error', jobs.start_research_retest(cycle['id']))
            self.assertIn('error', jobs.start_research_probe(cycle['id'], 'q01'))
            self.assertIn('error', jobs.start_run(cycle['test_plan'], {}, cycle_id=cycle['id']))
            started.assert_not_called(); config.assert_not_called()
        with self.assertRaises(ValueError): research.plan_from_report(cycle['external_baseline'])
        with self.assertRaises(ValueError): store.attach_retest(self.database, cycle['id'], report())
        self.post('/api/geo-content/cycles', {'project_id':self.project_id,'report':cycle['external_baseline']}, 400)
        self.assertEqual(self.counts()['geo_content_cycles'], 1)

    def test_editorial_analysis_and_draft_require_owner_facts_but_do_not_require_native_report(self):
        cycle = self.create()['cycle']
        self.assertIn('user_reported_external_not_independently_verified', research.research_prompt(cycle))
        with self.assertRaises(ValueError): research.draft_input(cycle, 'b01', 'zhihu')
        cycle = store.save_facts(self.database, cycle['id'], facts(), cycle['revision'])
        inputs = research.draft_input(cycle, 'b01', 'zhihu')
        self.assertEqual(inputs['baseline_basis'],'user_reported_external_not_independently_verified')
        saved = store.save_draft(self.database,cycle['id'],'b01','zhihu',draft(),{'method':'synthetic_work_writer'},cycle['revision'])
        self.assertIsInstance(saved['article_id'],int)
        self.assertEqual(store.get_cycle(self.database,cycle['id'])['baseline_source'],'user_reported_external')

    def test_profile_change_blocks_external_writing_but_reading_original_scope_remains_available(self):
        cycle = self.create()['cycle']
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE projects SET name=? WHERE id=?', ('另一个品牌',self.project_id))
        with self.assertRaises(external.ExternalBaselineConflict): store.save_facts(self.database,cycle['id'],facts(),cycle['revision'])
        self.assertEqual(store.get_cycle(self.database,cycle['id'])['profile']['name'],'合成外部云科技有限公司')
        self.assertEqual(self.client.get('/api/geo-content/cycles/'+cycle['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/geo-content/cycles').status_code, 200)
        with store._connect(self.database) as connection:
            connection.execute('BEGIN')
            self.assertEqual(store._cycle(connection,cycle['id'])['profile']['name'],'合成外部云科技有限公司')
        response = self.client.put('/api/geo-content/cycles/'+cycle['id'],json={'facts':facts(),'revision':cycle['revision']})
        self.assertEqual(response.status_code,409,response.get_json())
        scope={'project_id':self.project_id,'case_id':'q02','expected_revision':cycle['revision'],
               'expected_plan_sha256':cycle['test_plan']['plan_sha256'],'raw_response_text':'仍是旧轮的外部原文'}
        self.assertEqual(self.client.post('/api/geo-content/cycles/'+cycle['id']+'/external-observations',json=scope).status_code,409)
        with self.assertRaises(external.ExternalBaselineConflict):
            store.save_analysis(self.database,cycle['id'],'{}',{'method':'fixture'},cycle['revision'])
        with self.assertRaises(external.ExternalBaselineConflict):
            store.save_draft(self.database,cycle['id'],'b01','zhihu',draft(),{'method':'fixture'},cycle['revision'])

    def test_parallel_identical_requests_create_one_frozen_plan_and_one_cycle(self):
        freeze_body = self.freeze_payload()
        with ThreadPoolExecutor(max_workers=4) as workers:
            frozen = list(workers.map(lambda _: external.freeze(self.database,copy.deepcopy(freeze_body)),range(4)))
        self.assertEqual(len({item['frozen_plan']['id'] for item in frozen}),1)
        create_body = self.create_payload(frozen[0]['frozen_plan'])
        with ThreadPoolExecutor(max_workers=4) as workers:
            created = list(workers.map(lambda _: external.create(self.database,copy.deepcopy(create_body)),range(4)))
        self.assertEqual(len({item['cycle']['id'] for item in created}),1)
        self.assertEqual(self.counts()['geo_content_cycles'],1); self.assertEqual(self.counts()['geo_research_probes'],1)

    def test_new_routes_keep_operator_same_origin_strict_json_and_size_guards(self):
        paths = (self.prepare_url,self.freeze_url,self.create_url)
        for path in paths:
            self.assertEqual(self.client.post(path,data='{}',content_type='text/plain').status_code,415)
            for raw in ('null','[]','{"project_id":NaN}','{"project_id":1,"project_id":1}'):
                self.assertEqual(self.client.post(path,data=raw,content_type='application/json').status_code,400)
            self.assertEqual(self.client.post(path,json={},headers={'Origin':'https://foreign.example.org'}).status_code,403)
            with patch.dict(self.app.config,ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(self.client.post(path,json={}).status_code,401)
        self.assertEqual(self.client.post(self.freeze_url,data=b' '*(64*1024+1),content_type='application/json').status_code,413)
        before=self.counts();self.assertEqual(before['geo_content_cycles'],0)


if __name__=='__main__': unittest.main()
