import copy
import json
import tempfile
import unittest
from pathlib import Path

from db.createTable import initialize_database
from services.geo_entity_identity import same_entity_target
from services import geo_content_store as store
from services.geo_content_research import compare_reports
from services.geo_test_service import build_report, parse_ai_plan
from services.project_service import create_project, get_project


def scoped_report(*, path='/products/one/', entity_type='product', aliases=None, extra=None, run='before', legacy=False):
    profile = {'name': '独立测试对象', 'product': '独立测试对象', 'industry': '软件服务',
               'description': '提供文档处理工具', 'company': '示例运营公司',
               'audience': '研究团队', 'website': 'https://example.org'+path,
               'aliases': aliases or ['独立工具客户端']}
    if not legacy:
        profile.update(entity_type=entity_type, official_urls=extra or [])
    cases = [
        {'category':'discovery','question':'有哪些适合资料整理的桌面工具，请提供公开依据？','intent':'自然需求'},
        {'category':'brand_identity','question':'独立测试对象是什么，公开资料能核对哪些信息？','intent':'对象核验'},
        {'category':'website_guided','question':f'请查看 {profile["website"]} 核对产品和运营主体。','intent':'官网核验'},
    ]
    plan=parse_ai_plan(json.dumps({'cases':cases},ensure_ascii=False),profile,3,provenance={'method':'synthetic'})
    stamp='2026-10-21T00:00:00+00:00' if run!='before' else '2026-10-20T00:00:00+00:00'
    state={'run_id':run,'status':'completed','execution':{},'cases':{}}
    for case in plan['cases']:
        state['cases'][case['id']]={'status':'success','result':{
            'question':case['question'],'question_submitted':True,'answer_completed':True,
            'response':'独立测试对象提供资料处理功能。','timestamp':stamp,
            'collection_status':'success','source_extraction_status':'captured',
            'citations':[{'url':profile['website']}],'displayed_sources':[{'url':profile['website']}],
            'environment':{'client_mode':'consumer','model_label':'synthetic-model'}}}
    return build_report(plan,state)


class EntityContentBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.database=initialize_database(Path(self.temp.name)/'database.db')

    def test_full_page_identity_prevents_same_host_company_and_products_from_mixing(self):
        base={'name':'相同名称','website':'https://example.org/products/a','entity_type':'product'}
        self.assertTrue(same_entity_target(base,{**base,'website':'https://example.org:443/products/a/'}))
        for changes in ({'website':'https://example.org/products/b/'}, {'website':'https://example.org/'},
                        {'website':'https://example.org/products/ab/'},{'entity_type':'company'},
                        {'website':'https://www.example.org/products/a/'},{'website':'http://example.org/products/a/'}):
            with self.subTest(changes=changes):self.assertFalse(same_entity_target(base,{**base,**changes}))

    def test_new_cycle_auto_project_preserves_scoped_profile(self):
        report=scoped_report(extra=['https://service.example.org/'])
        cycle=store.create_cycle(self.database,report,'synthetic')
        project=get_project(self.database,cycle['project_id'])
        for field in ('entity_type','company','audience','aliases','official_urls'):
            self.assertEqual(project[field],report['profile'][field])
        with self.assertRaisesRegex(ValueError,'官网路径'):
            store.create_cycle(self.database,scoped_report(path='/products/two/',run='other'),'other',project['id'])
        with self.assertRaisesRegex(ValueError,'类型'):
            store.create_cycle(self.database,scoped_report(entity_type='company',run='company'),'other',project['id'])

    def test_retest_requires_same_entity_scope_and_aliases(self):
        baseline=scoped_report();cycle=store.create_cycle(self.database,baseline,'synthetic')
        store.attach_retest(self.database,cycle['id'],scoped_report(run='after'))
        for changes in ({'path':'/products/two/'},{'entity_type':'company'},
                        {'aliases':['全新的测量别名']},{'extra':['https://another.example.org/']},{'legacy':True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError,'类型|官网|口径|别名'):
                store.attach_retest(self.database,cycle['id'],scoped_report(run='changed',**changes))

    def test_legacy_and_strict_profiles_are_not_same_measurement_even_on_same_page(self):
        result=compare_reports(scoped_report(legacy=True),scoped_report(run='after'))
        self.assertFalse(result['conditions_match'])
        self.assertTrue(any('评估口径' in reason for reason in result['reasons']))

    def test_rendered_json_language_label_still_requires_one_complete_plan(self):
        report=scoped_report()
        raw=json.dumps({'cases':[{key:case[key] for key in ('category','question','intent')}
                                 for case in report['cases']]},ensure_ascii=False)
        plan=parse_ai_plan('json\n'+raw,report['profile'],3,provenance={'method':'synthetic'})
        self.assertEqual(plan['plan_sha256'],report['plan_sha256'])
        for suffix in ('\n不完整说明', '\n'+raw):
            with self.assertRaises(ValueError):
                parse_ai_plan('json\n'+raw+suffix,report['profile'],3,provenance={})
