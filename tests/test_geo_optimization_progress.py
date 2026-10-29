"""Synthetic project history only; no customer answers, credentials or network."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_optimization_progress as progress, geo_content_store as store
from services import geo_research_probe_service as probes
from services.geo_content_research import fingerprint
from services.project_service import create_project, delete_project, ProjectHasArticlesError
from tests.test_geo_content_loop import report
from tests.test_geo_tests import answer
from services.geo_test_service import normalize_profile, plan_fingerprint


class OptimizationProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.database = initialize_database(self.root/'database.db')
        baseline = report()
        baseline['profile'] = normalize_profile({**baseline['profile'], 'entity_type':'product'})
        baseline['plan_sha256'] = plan_fingerprint({'schema_version':'1.0','platform':'doubao_web', 'profile':baseline['profile'],
            'cases':[{key:case[key] for key in ('id','category','question','intent')} for case in baseline['cases']]})
        self.cycle = store.create_cycle(self.database, baseline, 'synthetic-progress')
        self.project_id = self.cycle['project_id']; self.case = self.cycle['test_plan']['cases'][1]
        self.before = self.external('  合成旧原文：尚未明确说明公开业务。\r\n')
        self.after = self.external('  合成新原文：出现了用户希望核对的业务文字。\r\n')
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root/'media'), COOKIES_DIRECTORY=str(self.root/'cookies')):
            from sau_backend import app
        config = patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        config.start(); self.addCleanup(config.stop)
        self.app = app; self.client = app.test_client()
        self.url = f'/api/geo-content/projects/{self.project_id}/progress'

    def external(self, raw, *, cycle=None, metadata=None, case_id=None):
        cycle = cycle or self.cycle
        return probes.attach_external_observation(self.database, cycle['id'], case_id or self.case['id'], raw, metadata or {},
            expected_scope={'project_id':cycle['project_id'],'expected_plan_sha256':cycle['test_plan']['plan_sha256'],'expected_revision':cycle['revision']})

    def payload(self, **changes):
        return {'request_id':uuid.uuid4().hex,'direction':'公开业务资料表达','action_summary':'合成操作摘要，待人工核对。',
                'status':'observed_improvement','before_probe_id':self.before['id'],'after_probe_id':self.after['id'],
                'before_quote':self.before['after']['response'],'after_quote':self.after['after']['response'],
                'expected_project_identity_sha256':self.identity(), **changes}

    def preparation(self, **changes):
        return {'request_id':uuid.uuid4().hex,'direction':'官网基础准备','action_summary':'合成建站准备，不表示搜索效果。','status':'preparation',
                'expected_project_identity_sha256':self.identity(), **changes}

    def identity(self, project_id=None):
        return progress.list_progress(self.database,project_id or self.project_id,include_probes=False)['project_identity_sha256']

    def post(self, body, status=200, url=None):
        response = self.client.post(url or self.url, json=body)
        self.assertEqual(response.status_code,status,response.get_json())
        return response.get_json()['data']

    def get(self, **query):
        response = self.client.get(self.url,query_string=query)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        return response.get_json()['data']

    def rewrite_probe(self, identifier, edit):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            record = json.loads(connection.execute('SELECT record FROM geo_research_probes WHERE id=?',(identifier,)).fetchone()[0])
            edit(record)
            connection.execute('UPDATE geo_research_probes SET record=?,created_at=? WHERE id=?',
                (json.dumps(record,ensure_ascii=False),record['created_at'],identifier))

    def count(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            return connection.execute('SELECT COUNT(*) FROM geo_optimization_progress').fetchone()[0]

    def legacy(self, template=None, *, recorded_at='2026-10-02T12:34:56.500000+00:00'):
        body = self.preparation()
        body.pop('expected_project_identity_sha256')
        if template is not None:
            body.update(status=template['status'], before_probe_id=template['before']['probe_id'],
                after_probe_id=template['after']['probe_id'], before_quote=template['before']['quote'], after_quote=template['after']['quote'])
        request_sha = fingerprint({'operation':'append_optimization_progress','project_id':self.project_id,'payload':body})
        record = {'id':uuid.uuid4().hex,'project_id':self.project_id,'request_id':body['request_id'],'request_sha256':request_sha,
            'direction':body['direction'],'action_summary':body['action_summary'],'status':body['status'],
            'status_label':progress.STATUSES[body['status']],'action_at':None,'recorded_at':recorded_at,
            'timeline_at':recorded_at,'timeline_basis':'recorded_at','review_note':'',
            'before':copy.deepcopy(template['before']) if template else None,'after':copy.deepcopy(template['after']) if template else None,
            'evidence_kind':template['evidence_kind'] if template else 'no_evidence',
            'assessment_basis':'operator_review_of_server_quotes','independent_verification':False,
            'native_metrics_available':False,'causal_effect':None,'limitations':list(progress.LIMITATIONS)}
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('''INSERT INTO geo_optimization_progress
                (id,project_id,request_id,request_sha256,status,evidence_kind,action_at,recorded_at,record)
                VALUES(?,?,?,?,?,?,?,?,?)''',(record['id'],self.project_id,uuid.UUID(body['request_id']).hex,request_sha,
                record['status'],record['evidence_kind'],None,recorded_at,json.dumps(record,ensure_ascii=False)))
        return body, record

    def stored_history(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute('SELECT id,record FROM geo_optimization_progress ORDER BY sequence').fetchall()

    def test_unknown_external_observation_preserves_quotes_conditions_and_separate_clocks(self):
        body = self.payload(action_at='2026-10-01T08:00:00+08:00',review_note='这是人工原文变化记录。')
        value = self.post(body); record = value['record']
        self.assertEqual(record['action_at'],'2026-10-01T00:00:00+00:00')
        self.assertEqual(record['timeline_at'],record['action_at']); self.assertEqual(record['timeline_basis'],'action_at')
        self.assertEqual(record['before']['quote'],body['before_quote']); self.assertEqual(record['after']['quote'],body['after_quote'])
        for snapshot in (record['before'],record['after']):
            self.assertIsNone(snapshot['collected_at']); self.assertFalse(snapshot['collection_time_known'])
            self.assertIsNotNone(snapshot['recorded_at']); self.assertIsNone(snapshot['answer_evaluable'])
            self.assertEqual(snapshot['response_sha256'],hashlib.sha256(snapshot['quote'].encode()).hexdigest())
            self.assertEqual(snapshot['response_sha256_basis'],'stored_and_verified')
            self.assertTrue(all(item is None for item in snapshot['conditions'].values()))
        self.assertFalse(record['independent_verification']); self.assertFalse(record['native_metrics_available']); self.assertIsNone(record['causal_effect'])
        self.assertEqual(record['evidence_kind'],'user_reported_included')
        self.assertEqual(store.get_cycle(self.database,self.cycle['id'])['facts'],[])
        data = self.get(); self.assertEqual(data['counts']['observed_improvement'],1)
        self.assertEqual(data['counts']['independently_verified_effects'],0); self.assertEqual(data['counts']['user_reported_included'],1)

    def test_same_request_payload_reuses_one_row_changed_payload_conflicts_and_concurrent_retry_is_atomic(self):
        body = self.payload(); first = self.post(body); original = self.database.read_bytes()
        again = self.post(body); self.assertEqual(self.database.read_bytes(),original)
        self.assertEqual(first['record'],again['record']); self.assertTrue(again['receipt']['idempotent'])
        self.assertEqual(first['receipt']['request_sha256'],fingerprint({'operation':'append_optimization_progress','project_id':self.project_id,'payload':body}))
        self.post({**body,'review_note':'changed'},409)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _:progress.append_progress(self.database,self.project_id,copy.deepcopy(body)),range(4)))
        self.assertEqual({item['record']['id'] for item in results},{first['record']['id']}); self.assertEqual(self.count(),1)

    def test_preparation_is_not_an_effect_and_preparation_only_project_cannot_be_deleted(self):
        project = create_project(self.database,name='合成准备项目',website='https://preparation.example.org/',
            product='合成产品',industry='合成行业',description='',keywords=[],competitors=[])
        url = f'/api/geo-content/projects/{project["id"]}/progress'
        record = self.post(self.preparation(expected_project_identity_sha256=self.identity(project['id'])),url=url)['record']
        self.assertIsNone(record['before']); self.assertIsNone(record['after'])
        self.assertEqual(record['evidence_kind'],'no_evidence')
        data = self.client.get(url).get_json()['data']
        self.assertEqual(data['counts']['preparation'],1); self.assertEqual(data['counts']['observed_improvement'],0)
        with self.assertRaises(ProjectHasArticlesError):delete_project(self.database,project['id'])
        self.assertEqual(self.client.delete('/api/projects/'+str(project['id'])).status_code,409)
        other = create_project(self.database,name='合成可删除项目',website='https://deletable.example.org/',
            product='',industry='',description='',keywords=[],competitors=[])
        delete_project(self.database,other['id'])
        self.assertEqual(self.get()['records'],[])

    def test_immutable_rows_and_snapshots_remain_after_original_statement_or_project_changes(self):
        saved = self.post(self.payload())['record']
        self.rewrite_probe(self.after['id'],lambda record:record['conditions'].update(same_question=True))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET description=? WHERE id=?',('新的合成说明',self.project_id))
            for statement in ('UPDATE geo_optimization_progress SET status="preparation"','DELETE FROM geo_optimization_progress'):
                with self.assertRaises(sqlite3.IntegrityError):connection.execute(statement)
        self.assertEqual(self.get()['records'][0],saved)

    def test_project_namespace_and_probe_existence_never_leak_other_project(self):
        other = create_project(self.database,name='合成隔离项目',website='https://isolated.example.org/',
            product='',industry='',description='',keywords=[],competitors=[])
        foreign = f'/api/geo-content/projects/{other["id"]}/progress'
        self.post(self.payload(expected_project_identity_sha256=self.identity(other['id'])),404,url=foreign)
        self.assertEqual(self.client.get(foreign).get_json()['data']['probe_options'],[])
        self.post(self.preparation(expected_project_identity_sha256=self.identity(other['id'])),url=foreign)
        self.assertEqual(self.get()['counts']['total'],0)
        self.assertEqual(self.client.get('/api/geo-content/projects/999999/progress').status_code,404)
        self.post(self.preparation(),404,url='/api/geo-content/projects/999999/progress')
        self.post(self.payload(after_probe_id='missing-probe'),404)

    def test_both_comparison_stages_require_distinct_records_and_exact_nonempty_quotes(self):
        for status in ('observed_improvement','not_observed'):
            for change in ({'after_probe_id':None,'after_quote':''},{'after_probe_id':self.before['id']},
                           {'before_quote':''},{'before_quote':'仅有实际原文没有的词'}):
                self.post(self.payload(status=status,**change),409 if change.get('before_quote')=='仅有实际原文没有的词' else 400)
        self.assertEqual(self.count(),0)

    def test_actual_observed_question_takes_priority_over_the_same_frozen_anchor(self):
        different = self.external('合成第三条原文。',metadata={'question':'合成实际问题已经改变。'})
        self.post(self.payload(after_probe_id=different['id'],after_quote=different['after']['response']),409)
        other = self.external('合成另一道题的原文。',case_id=self.cycle['test_plan']['cases'][0]['id'])
        self.post(self.payload(after_probe_id=other['id'],after_quote=other['after']['response']),409)

    def test_same_text_and_case_from_a_different_plan_cannot_be_comparison_evidence(self):
        baseline = copy.deepcopy(self.cycle['baseline'])
        baseline['profile']['description'] = '另一份合成资料范围'
        baseline['plan_sha256'] = plan_fingerprint({'schema_version':'1.0','platform':'doubao_web',
            'profile':baseline['profile'],'cases':[{key:case[key] for key in ('id','category','question','intent')} for case in baseline['cases']]})
        different = store.create_cycle(self.database,baseline,'different-synthetic-plan',self.project_id)
        probe = self.external('不同计划但相同题文的合成原文。',cycle=different)
        self.post(self.payload(after_probe_id=probe['id'],after_quote=probe['after']['response']),409)

    def test_server_raw_question_and_plan_digest_tampering_is_rejected_without_a_row(self):
        for edit in (lambda record:record.update(response_sha256='0'*64),
                     lambda record:record['after'].update(response='被篡改的合成原文'),
                     lambda record:record.update(question_sha256='0'*64),
                     lambda record:record.update(plan_sha256='0'*64),
                     lambda record:record.update(observed_question='新的题文',observed_question_sha256='0'*64)):
            with self.subTest(edit=edit):
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    original = connection.execute('SELECT record FROM geo_research_probes WHERE id=?',(self.after['id'],)).fetchone()[0]
                self.rewrite_probe(self.after['id'],edit);self.post(self.payload(),409)
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    connection.execute('UPDATE geo_research_probes SET record=? WHERE id=?',(original,self.after['id']))
        self.assertEqual(self.count(),0)

    def test_excluded_or_explicitly_incomplete_history_cannot_become_improvement(self):
        for side in ('before','after'):
            identifier = getattr(self,side)['id']
            for edit in (lambda record:record['after'].update(answer_evaluable=False),
                         lambda record:record['after'].update(answer_error='private_account_context'),
                         lambda record:record.update(excluded=True)):
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    original = connection.execute('SELECT record FROM geo_research_probes WHERE id=?',(identifier,)).fetchone()[0]
                self.rewrite_probe(identifier,edit);self.post(self.payload(),409)
                self.assertTrue(next(item for item in self.get()['probe_options'] if item['probe_id']==identifier)['excluded_from_improvement'])
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    connection.execute('UPDATE geo_research_probes SET record=? WHERE id=?',(original,identifier))
        self.assertEqual(self.count(),0)

    def test_reverse_recorded_or_known_collection_times_cannot_be_labeled_improvement(self):
        self.rewrite_probe(self.before['id'],lambda record:record.update(created_at='2026-10-02T10:00:00Z'))
        self.rewrite_probe(self.after['id'],lambda record:record.update(created_at='2026-10-01T10:00:00Z'))
        self.post(self.payload(),409)
        record = self.post(self.payload(status='not_observed'))['record']
        self.assertIsNone(record['before']['collected_at']); self.assertIsNone(record['after']['collected_at'])
        self.rewrite_probe(self.after['id'],lambda record:record.update(created_at='2026-10-03T10:00:00Z'))
        self.rewrite_probe(self.before['id'],lambda record:record['after'].update(collected_at='2026-10-02T10:00:00Z'))
        self.rewrite_probe(self.after['id'],lambda record:record['after'].update(collected_at='2026-10-01T10:00:00Z'))
        self.post(self.payload(),409)

    def test_native_evidence_retains_its_source_but_does_not_manufacture_raw_integrity_or_effect(self):
        native = []
        for index in range(2):
            native.append(probes.attach_probe(self.database,self.cycle['id'],
                {'case_id':self.case['id'],'collector_result':answer(self.case['question'],response=f'合成原生文字{index}',
                  environment={'client_mode':'consumer','model_label':'synthetic'},timestamp='2026-10-01T00:00:00Z'),
                 'run_id':uuid.uuid4().hex},source='native_collector',native_task_id='synthetic-task'))
        body = self.payload(before_probe_id=native[0]['id'],after_probe_id=native[1]['id'],
            before_quote=native[0]['after']['response'],after_quote=native[1]['after']['response'])
        record = self.post(body)['record']
        self.assertEqual(record['evidence_kind'],'native_only'); self.assertTrue(record['after']['native_verified'])
        self.assertEqual(record['after']['response_sha256_basis'],'derived_from_server_record')
        self.assertFalse(record['independent_verification']); self.assertFalse(record['native_metrics_available'])
        self.assertEqual(self.get()['counts']['independently_verified_effects'],0)

    def test_get_is_read_only_bounded_and_never_returns_raw_authentication_or_foreign_evidence(self):
        self.rewrite_probe(self.after['id'],lambda record:record.update(authentication={'token':'synthetic-private'},evidence={'secret':'synthetic-private'}))
        self.rewrite_probe(self.after['id'],lambda record:record['conditions'].update(authentication='synthetic-private'))
        before = self.database.read_bytes(); data = self.get()
        self.assertEqual(self.database.read_bytes(),before)
        encoded = json.dumps(data,ensure_ascii=False)
        for prohibited in ('raw_response_text','synthetic-private','authentication','"response":'):
            self.assertNotIn(prohibited,encoded)
        for item in data['probe_options']:
            self.assertLessEqual(len(item['response_excerpt']),1000); self.assertNotIn('quote',item)

    def test_probe_options_page_is_bounded_and_uses_only_this_project(self):
        for index in range(101):self.external(f'合成可选记录第{index}条。')
        first = self.get(); second = self.get(probe_offset=100)
        self.assertEqual(len(first['probe_options']),100); self.assertTrue(first['probe_options_has_more'])
        self.assertEqual(first['next_probe_offset'],100); self.assertEqual(len(second['probe_options']),3)
        self.assertFalse(second['probe_options_has_more']); self.assertIsNone(second['next_probe_offset'])
        self.assertEqual(len({row['probe_id'] for row in first['probe_options']+second['probe_options']}),103)

    def test_user_context_statements_keep_user_basis_and_whitelist_private_metadata(self):
        question = self.case['question']; question_sha = hashlib.sha256(question.encode()).hexdigest()
        def edit(record):
            record.update(observed_question=question,observed_question_sha256=question_sha,context_attestations=[
                {'id':'synthetic-statement','basis':'user_statement','native_verified':False,'independent_verification':False,
                 'response_sha256':record['response_sha256'],'question':question,'question_sha256':question_sha,
                 'new_conversation':True,'first_message':True,'confirmation_text':'合成用户声明，非独立核验。',
                 'authentication':'synthetic-private','prior_context':{'same_question':None,'password':'synthetic-private'}}])
        self.rewrite_probe(self.after['id'],edit)
        saved = self.post(self.payload())['record']['after']; statement = saved['context_attestations'][0]
        self.assertEqual(statement['basis'],'user_statement'); self.assertFalse(statement['independent_verification'])
        self.assertNotIn('synthetic-private',json.dumps(saved)); self.assertEqual(saved['question_basis'],'observed_question')

    def test_stable_export_snapshot_pagination_excludes_later_backdated_appends(self):
        for index in range(23):self.post(self.preparation(action_at=f'2026-10-{index+1:02d}T12:00:00Z'))
        first = self.get(include_probes='false'); self.assertEqual(len(first['records']),20); self.assertTrue(first['has_more'])
        self.assertEqual(first['counts']['total'],23); self.assertEqual(first['probe_options'],[])
        self.post(self.preparation(action_at='2026-10-10T12:30:00Z'))
        second = self.get(offset=20,snapshot_sequence=first['snapshot_sequence'],include_probes='false')
        self.assertEqual(len(second['records']),3); self.assertFalse(second['has_more']); self.assertEqual(second['counts']['total'],23)
        ids = [item['id'] for item in first['records']+second['records']]
        self.assertEqual(len(set(ids)),23)
        times = [item['timeline_at'] for item in first['records']+second['records']]
        self.assertEqual(times,sorted(times,reverse=True)); self.assertEqual(self.get()['counts']['total'],24)

    def test_new_identity_snapshot_sha_survives_json_roundtrip_and_counts_only_current_entity(self):
        first = self.get(); body = self.payload(); saved = self.post(body)['record']
        self.assertEqual(saved['project_identity'],first['project_identity'])
        self.assertEqual(saved['project_identity_sha256'],fingerprint(saved['project_identity']))
        self.assertEqual(saved['project_identity_basis'],'stored_at_record'); self.assertIs(saved['current_entity_match'],True)
        before = self.database.read_bytes(); listed = self.get()
        self.assertEqual(self.database.read_bytes(),before); self.assertEqual(listed['records'][0],saved)
        self.assertEqual(listed['counts_current']['total'],1); self.assertEqual(listed['counts_history']['total'],0)
        self.assertEqual(listed['counts_unknown']['total'],0)

    def test_rename_type_full_website_path_alias_or_official_scope_cannot_reuse_old_evidence(self):
        saved = self.post(self.preparation())['record']; original = saved['project_identity']
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute('SELECT name,entity_type,website,aliases,official_urls FROM projects WHERE id=?',(self.project_id,)).fetchone()
        fields = ('name','entity_type','website','aliases','official_urls')
        old_body = self.payload()
        changes = ({'name':'另一合成对象'},{'entity_type':'company'},
            {'website':self.cycle['profile']['website'].rstrip('/')+'/another-product'},
            {'aliases':json.dumps(['新的合成别名'])},{'official_urls':json.dumps(['https://scope.example.org/docs'])})
        for change in changes:
            with self.subTest(change=change):
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    values = dict(zip(fields,row)); values.update(change)
                    connection.execute('UPDATE projects SET name=?,entity_type=?,website=?,aliases=?,official_urls=? WHERE id=?',
                        (*[values[field] for field in fields],self.project_id))
                self.post(old_body,409)
                self.post(self.payload(),409)  # Fresh CAS cannot relabel old-cycle evidence either.
                listed = self.get(); self.assertEqual(listed['probe_options'],[])
                self.assertEqual(listed['counts_current']['total'],0); self.assertEqual(listed['counts_history']['total'],1)
                self.assertEqual(listed['records'][0]['project_identity'],original)
                self.assertIs(listed['records'][0]['current_entity_match'],False)
        self.assertEqual(self.count(),1)

    def test_preparation_old_form_is_rejected_atomically_after_identity_change(self):
        body = self.preparation()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET name=? WHERE id=?',('另一个合成客户',self.project_id))
        before = self.database.read_bytes(); self.post(body,409)
        self.assertEqual(self.database.read_bytes(),before); self.assertEqual(self.count(),0)
        self.post(self.preparation())
        self.assertEqual(self.get()['counts_current']['preparation'],1)

    def test_exact_legacy_payload_reuses_immutable_receipt_without_new_scope_field(self):
        body, stored = self.legacy()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET name=?,updated_at=? WHERE id=?',
                ('另一个合成客户','2026-10-03 00:00:00',self.project_id))
        before = self.stored_history(); result = self.post(body)
        self.assertTrue(result['receipt']['idempotent']); self.assertEqual(result['record']['id'],stored['id'])
        self.assertNotIn('expected_project_identity_sha256',body)
        self.assertIsNone(result['record']['current_entity_match']); self.assertEqual(self.stored_history(),before)
        self.post({**body,'expected_project_identity_sha256':self.identity()},409)
        self.post({**body,'review_note':'changed'},409)
        self.post({**body,'request_id':uuid.uuid4().hex},400)
        self.assertEqual(self.count(),1)

    def test_legacy_comparison_identity_is_derived_from_frozen_cycle_and_kept_as_history(self):
        current = self.post(self.payload())['record']; _, legacy = self.legacy(current)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET name=?,updated_at=? WHERE id=?',
                ('另一个合成客户','2026-10-03 00:00:00',self.project_id))
        before = self.stored_history(); listed = self.get(); after = self.stored_history()
        derived = next(item for item in listed['records'] if item['id']==legacy['id'])
        self.assertEqual(derived['project_identity']['name'],self.cycle['profile']['name'])
        self.assertEqual(derived['project_identity_basis'],'derived_frozen_profile'); self.assertIs(derived['current_entity_match'],False)
        self.assertEqual(listed['counts_current']['total'],0); self.assertEqual(listed['counts_history']['total'],2)
        self.assertEqual(before,after); self.assertNotIn('project_identity',json.loads(next(value for identifier,value in before if identifier==legacy['id'])))

    def test_legacy_preparation_utc_second_watermark_is_strict_and_read_only(self):
        _, legacy = self.legacy()
        for updated, matches in (('2026-10-02 12:34:55',True),('2026-10-02 12:34:56',None),
                                 ('2026-10-02 12:34:57',None),('2026-10-02T12:34:55Z',None),('invalid',None)):
            with self.subTest(updated=updated):
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    connection.execute('UPDATE projects SET updated_at=? WHERE id=?',(updated,self.project_id))
                original = self.stored_history(); original_bytes = self.database.read_bytes(); listed = self.get()
                projected = listed['records'][0]
                self.assertIs(projected['current_entity_match'],matches)
                self.assertEqual(projected['project_identity_basis'],'derived_unchanged_project' if matches else 'unknown')
                self.assertEqual(listed['counts_current']['total'],1 if matches else 0)
                self.assertEqual(listed['counts_unknown']['total'],0 if matches else 1)
                self.assertEqual(self.stored_history(),original); self.assertEqual(self.database.read_bytes(),original_bytes)
                self.assertEqual(projected['id'],legacy['id'])

    def test_paged_export_identity_cas_rejects_rename_even_with_the_same_append_snapshot(self):
        self.post(self.preparation()); first = self.get(include_probes='false')
        self.get(include_probes='false',snapshot_sequence=first['snapshot_sequence'],
            expected_project_identity_sha256=first['project_identity_sha256'])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE projects SET website=? WHERE id=?',('https://changed.example.org/new-client',self.project_id))
        response = self.client.get(self.url,query_string={'snapshot_sequence':first['snapshot_sequence'],
            'expected_project_identity_sha256':first['project_identity_sha256'],'include_probes':'false'})
        self.assertEqual(response.status_code,409,response.get_json())
        self.assertEqual(self.get()['counts_history']['preparation'],1)

    def test_fields_uuid_times_stage_bounds_and_namespace_queries_are_finite(self):
        for change in ({'request_id':'request-arbitrary'},{'direction':''},{'action_summary':'x'*3001},{'review_note':'x'*3001},
                       {'before_quote':'x'*2001},{'action_at':'2026-10-01T10:00:00'},{'status':[]},
                       {'native_verified':True},{'snapshot':{}},{'rank':1},{'success_rate':1}):
            self.post({**self.payload(),**change},400)
        self.post(self.preparation(request_id=str(uuid.uuid4())))
        for query in ({'raw':True},{'offset':-1},{'offset':1000001},{'snapshot_sequence':999999},{'include_probes':'yes'}):
            self.assertEqual(self.client.get(self.url,query_string=query).status_code,400)
        self.assertEqual(self.client.get(self.url,query_string=[('offset','0'),('offset','1')]).status_code,400)
        self.assertEqual(self.count(),1)

    def test_existing_operator_same_origin_json_size_and_method_guards_remain_active(self):
        self.assertEqual(self.client.post(self.url,data='{}',content_type='text/plain').status_code,415)
        for raw in ('null','[]','{"status":NaN}','{"request_id":"a","request_id":"b"}'):
            self.assertEqual(self.client.post(self.url,data=raw,content_type='application/json').status_code,400)
        self.assertEqual(self.client.post(self.url,data=b' '*(64*1024+1),content_type='application/json').status_code,413)
        for method in ('get','post'):
            self.assertEqual(getattr(self.client,method)(self.url,headers={'Origin':'https://foreign.example.org'}).status_code,403 if method=='get' else 415)
            with patch.dict(self.app.config,ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(getattr(self.client,method)(self.url).status_code,401)
        self.assertEqual(self.client.put(self.url,json={}).status_code,405);self.assertEqual(self.client.delete(self.url).status_code,405)
        self.assertEqual(self.count(),0)


if __name__ == '__main__':unittest.main()
