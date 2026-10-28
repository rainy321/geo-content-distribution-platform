"""Manual official portal progress: fixture databases only, never portal requests."""
import copy
import json
import os
import sqlite3
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from services import geo_content_store as store
from services import geo_search_onboarding as onboarding
from services import webmaster_service as webmaster
from tests.test_geo_research_probes import ProbeFixture


class SearchOnboardingTests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(os.environ, DATABASE_PATH=str(self.database), DEMO_MODE='false', SEED_DEMO_DATA='false',
                        MEDIA_ROOT=str(self.root/'media'), COOKIES_DIRECTORY=str(self.root/'cookies')):
            from sau_backend import app
        config=patch.dict(app.config, DATABASE_PATH=self.database, TESTING=True, ACCESS_CONTROL_ENABLED=False)
        config.start();self.addCleanup(config.stop)
        self.app,self.client=app,app.test_client()
        self.url=f"/api/geo-content/cycles/{self.cycle['id']}/search-onboarding"

    def view(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        return response.get_json()['data']

    def payload(self,provider='baidu',progress=None):
        view=self.view();record=next(item['record'] for item in view['providers'] if item['id']==provider)
        return {'project_id':view['project_id'],'expected_plan_sha256':view['plan_sha256'],
            'expected_revision':view['revision'],'expected_context_sha256':view['context_sha256'],
            'provider':provider,'expected_provider_revision':record['revision'] if record else 0,
            'progress':progress or {'site_added':None,'ownership_confirmed':None,'sitemap_status':'unknown','note':''}}

    def save(self,payload):
        response=self.client.post(self.url,json=payload)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        return response.get_json()['data']

    def stored(self):
        with closing(sqlite3.connect(self.database)) as connection,connection:
            return json.loads(connection.execute('SELECT record FROM geo_webmaster_records WHERE cycle_id=?',(self.cycle['id'],)).fetchone()[0])

    def replace(self,value):
        with closing(sqlite3.connect(self.database)) as connection,connection:
            connection.execute(webmaster.CREATE_TABLE)
            connection.execute('INSERT OR REPLACE INTO geo_webmaster_records VALUES(?,?,?)',
                (self.cycle['id'],json.dumps(value,ensure_ascii=False),'fixture'))

    def other_tables(self):
        with closing(sqlite3.connect(self.database)) as connection,connection:
            return {name:connection.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()
                for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'") if name!='geo_webmaster_records'}

    def test_get_is_read_only_fixed_official_providers_unknown_and_no_network(self):
        before=self.database.read_bytes()
        with patch('socket.getaddrinfo',side_effect=AssertionError('no DNS')),patch('requests.get',side_effect=AssertionError('no fetch')):
            view=self.view()
        self.assertEqual(self.database.read_bytes(),before)
        self.assertTrue(view['supported']);self.assertEqual(view['site_origin'],'https://synthetic.example.org')
        self.assertEqual([item['id'] for item in view['providers']],['baidu','toutiao'])
        self.assertTrue(all(item['record'] is None for item in view['providers']))
        self.assertNotIn('score',view);self.assertNotIn('success',view)
        self.assertEqual(view['sitemap_url'],'https://synthetic.example.org/sitemap.xml')

    def test_roundtrip_preserves_exact_unknown_false_note_and_sibling_json_and_all_cycle_tables(self):
        siblings={'sites':{'bing-fixture':{'ownership_confirmed':True}},'website_audit':{'history':['synthetic']},'future_key':{'keep':True}}
        self.replace(siblings);before=self.other_tables()
        body=self.payload(progress={'site_added':False,'ownership_confirmed':None,'sitemap_status':'unknown','note':'  未核对\r\n保留空格。  '})
        with patch('socket.getaddrinfo',side_effect=AssertionError('no DNS')),patch('requests.post',side_effect=AssertionError('no submission')):
            data=self.save(body)
        record=data['overview']['providers'][0]['record']
        self.assertEqual(record['progress'],body['progress']);self.assertEqual(record['revision'],1)
        self.assertFalse(record['software_verified']);self.assertFalse(record['software_submitted'])
        self.assertEqual(record['evidence_source'],'user_reported_platform_progress')
        self.assertEqual(self.other_tables(),before)
        self.assertEqual({key:self.stored()[key] for key in siblings},siblings)
        self.assertIsNone(self.view()['providers'][1]['record'])
        self.assertEqual(onboarding.get_overview(self.database,self.cycle['id'])['providers'][0]['record'],record)
        self.assert_original_unchanged()

    def test_exact_duplicate_is_read_only_and_stale_edit_cannot_overwrite_or_rewind(self):
        body=self.payload();first=self.save(body);before=self.database.read_bytes()
        repeated=self.save(copy.deepcopy(body))
        self.assertTrue(repeated['receipt']['idempotent']);self.assertEqual(first['overview'],repeated['overview'])
        self.assertEqual(self.database.read_bytes(),before)
        stale=copy.deepcopy(body);stale['progress']['note']='different'
        self.assertEqual(self.client.post(self.url,json=stale).status_code,400)
        current=self.payload();current['progress']['note']='second';self.save(current)
        self.assertEqual(self.client.post(self.url,json=body).status_code,400)
        self.assertEqual(self.view()['providers'][0]['record']['revision'],2)

    def test_concurrent_same_request_creates_one_revision_and_providers_do_not_overwrite(self):
        body=self.payload()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:onboarding.save_progress(self.database,self.cycle['id'],body),range(2)))
        self.assertEqual(sorted(result['receipt']['idempotent'] for result in results),[False,True])
        self.save(self.payload('toutiao',{'site_added':True,'ownership_confirmed':False,'sitemap_status':'pending','note':'平台显示处理中'}))
        self.assertEqual([item['record']['revision'] for item in self.view()['providers']],[1,1])
        self.assertEqual(len(self.stored()['other_search_entries']),2)

    def test_exact_project_plan_revision_context_and_provider_version_guards(self):
        original=self.payload();before=self.database.read_bytes()
        for field,value in [('project_id',999),('project_id',True),('expected_plan_sha256','b'*64),
                ('expected_revision',original['expected_revision']+1),('expected_revision',True),
                ('expected_context_sha256','b'*64),('expected_provider_revision',1),('expected_provider_revision',False)]:
            with self.subTest(field=field,value=value):
                payload=copy.deepcopy(original);payload[field]=value
                self.assertEqual(self.client.post(self.url,json=payload).status_code,400)
                self.assertEqual(self.database.read_bytes(),before)

    def test_facts_revision_changes_reject_unsaved_but_allow_same_known_request_recovery(self):
        body=self.payload();self.save(body);identity=self.view()['identity_sha256']
        self.cycle=store.save_facts(self.database,self.cycle['id'],[{'text':'合成已确认材料','source_url':'','confirmed':True}],self.cycle['revision'])
        view=self.view();self.assertEqual(view['identity_sha256'],identity)
        self.assertNotEqual(view['context_sha256'],body['expected_context_sha256'])
        before=self.database.read_bytes();self.assertTrue(self.save(body)['receipt']['idempotent'])
        self.assertEqual(self.database.read_bytes(),before)
        changed=copy.deepcopy(body);changed['progress']['note']='new after revision'
        self.assertEqual(self.client.post(self.url,json=changed).status_code,400)

    def test_new_company_or_plan_scope_keeps_old_history_and_starts_unknown(self):
        old=self.payload();self.save(old);old_entries=copy.deepcopy(self.stored()['other_search_entries'])
        changed=copy.deepcopy(self.cycle);changed['profile']['name']='另一家合成企业'
        with closing(store._connect(self.database)) as connection:
            view=onboarding.overview(connection,changed)
        self.assertNotEqual(view['identity_sha256'],next(iter(old_entries.values()))['identity_sha256'])
        self.assertTrue(all(item['record'] is None for item in view['providers']))
        self.assertEqual(self.stored()['other_search_entries'],old_entries)
        self.assertEqual(self.view()['providers'][0]['record']['identity_sha256'],next(iter(old_entries.values()))['identity_sha256'])

    def test_unsupported_http_private_and_credential_sites_do_not_fetch_or_create_records(self):
        before=self.database.read_bytes()
        for address in ['http://example.org','https://127.0.0.1','https://internal.local','https://name:secret@example.org']:
            changed=copy.deepcopy(self.cycle);changed['profile']['website']=address
            with closing(store._connect(self.database)) as connection,patch('socket.getaddrinfo',side_effect=AssertionError('no DNS')):
                result=onboarding.overview(connection,changed)
            self.assertFalse(result['supported'],address);self.assertEqual(result['providers'],[])
            self.assertEqual(self.database.read_bytes(),before)

    def test_nullable_legacy_scope_requires_exact_null_and_missing_entity_uses_product(self):
        legacy=copy.deepcopy(self.cycle);legacy['project_id']=None
        legacy['profile'].pop('entity_type');legacy['profile'].pop('official_urls')
        with patch('services.geo_content_store._cycle',return_value=legacy):
            body=self.payload();self.assertIsNone(body['project_id'])
            wrong={**body,'project_id':self.cycle['project_id']}
            self.assertEqual(self.client.post(self.url,json=wrong).status_code,400)
            result=self.save(body);record=result['overview']['providers'][0]['record']
            self.assertIsNone(record['scope']['project_id']);self.assertEqual(record['scope']['profile']['entity_type'],'product')
            self.assertEqual(record['scope']['profile']['official_urls'],[])
            self.assertTrue(self.save(body)['receipt']['idempotent'])

    def test_bounded_history_refuses_capacity_without_evicting_an_old_scope(self):
        self.save(self.payload());stored=self.stored();prototype=next(iter(stored['other_search_entries'].values()))
        entries={}
        for index in range(16):
            record=copy.deepcopy(prototype);record['scope']['profile']['name']=f'历史合成对象{index}'
            record['identity_sha256']=onboarding._digest(record['scope'])
            entries[onboarding._key(record['provider'],record['identity_sha256'])]=record
        stored['other_search_entries']=entries;self.replace(stored)
        body=self.payload();before=self.database.read_bytes()
        response=self.client.post(self.url,json=body)
        self.assertEqual(response.status_code,400);self.assertEqual(self.database.read_bytes(),before)
        self.assertEqual(self.stored()['other_search_entries'],entries)

    def test_finite_progress_rejects_tokens_native_fields_arbitrary_urls_and_contradictions(self):
        original=self.payload();before=self.database.read_bytes()
        invalid=[{**original,'token':'secret'},{**original,'provider':'bing'},{**original,'provider':['baidu']},
            {**original,'progress':{**original['progress'],'url':'https://other.example.org'}},
            {**original,'progress':{**original['progress'],'native_verified':True}},
            {**original,'progress':{**original['progress'],'geo_success':True}},
            {**original,'progress':{**original['progress'],'site_added':1}},
            {**original,'progress':{**original['progress'],'site_added':False,'ownership_confirmed':True}},
            {**original,'progress':{**original['progress'],'sitemap_status':'indexed'}},
            {**original,'progress':{**original['progress'],'note':'字'*501}},
            {**original,'progress':{**original['progress'],'note':'\ud800'}}]
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.assertEqual(self.client.post(self.url,json=value).status_code,400)
                self.assertEqual(self.database.read_bytes(),before)

    def test_corrupted_stored_records_are_rejected_without_silent_defaults_or_repair(self):
        self.save(self.payload());valid=self.stored();key=next(iter(valid['other_search_entries']))
        edits=[('site_origin','https://another.example.org'),('identity_sha256','a'*64),('request_sha256',None),
            ('revision',True),('software_verified',True),('updated_at','yesterday'),('scope',{'cycle_id':'other'})]
        for field,value in edits:
            damaged=copy.deepcopy(valid);damaged['other_search_entries'][key][field]=value;self.replace(damaged)
            before=self.database.read_bytes()
            self.assertEqual(self.client.get(self.url).status_code,400,(field,value))
            self.assertEqual(self.database.read_bytes(),before)
        for mutate in [lambda r:r.pop('updated_at'),lambda r:r.update(extra='ignored'),
                       lambda r:r['scope'].update(cycle_id='other'),lambda r:r['scope']['profile'].update(name='近名公司')]:
            damaged=copy.deepcopy(valid);mutate(damaged['other_search_entries'][key]);self.replace(damaged)
            self.assertEqual(self.client.get(self.url).status_code,400)

    def test_api_auth_same_origin_json_limits_duplicate_keys_missing_cycle_and_no_store(self):
        body=self.payload()
        for method in ('get','post'):
            response=getattr(self.client,method)(self.url,headers={'Origin':'https://foreign.example.org'},**({'json':body} if method=='post' else {}))
            self.assertEqual(response.status_code,403);self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.post(self.url,json=body,headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        for data,status,content in [('{}',415,'text/plain'),('[]',400,'application/json'),
                ('{"provider":"baidu","provider":"toutiao"}',400,'application/json'),
                ('{"value":NaN}',400,'application/json'),('x'*16385,413,'application/json')]:
            self.assertEqual(self.client.post(self.url,data=data,content_type=content).status_code,status)
        self.assertEqual(self.client.get(self.url+'?project_id=999').status_code,400)
        self.assertEqual(self.client.get(self.url,data='body').status_code,400)
        self.assertEqual(self.client.get('/api/geo-content/cycles/missing/search-onboarding').status_code,404)
        with patch.dict(self.app.config,ACCESS_CONTROL_ENABLED=True):
            for method in ('get','post'):
                response=getattr(self.client,method)(self.url,**({'json':body} if method=='post' else {}))
                self.assertEqual(response.status_code,401);self.assertEqual(response.headers['Cache-Control'],'no-store')


if __name__=='__main__':unittest.main()
