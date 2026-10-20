"""Company/product measurements opt in; legacy plans retain their own semantics."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from services.geo_test_service import (assess_answer,normalize_profile,canonical_entity_url,
    parse_ai_plan,validate_plan,plan_fingerprint,build_planning_prompt,build_report)
from services.geo_test_reports import project_report_evaluation,list_saved_reports,read_saved_report
from tests.test_geo_tests import profile,plan,answer


def target(**changes):
    return {**profile(),'entity_type':'product','name':'懂AI中转站','product':'模型 API 服务',
        'website':'https://tongyuye.com/products/dongai/','official_urls':['https://api.tongyuye.com/'],
        'aliases':['DongAI 中转站'],**changes}


def questions(data):
    return {'cases':[
        {'category':'discovery','question':'企业应如何选择并核对多模型接口服务？','intent':'自然需求'},
        {'category':'brand_identity','question':data['name']+'有哪些公开业务说明？','intent':'当前对象核验'},
        {'category':'website_guided','question':'请访问 '+data['website']+' 核对当前对象。','intent':'官网核验'}]}


def make_plan(data):
    return parse_ai_plan(json.dumps(questions(data),ensure_ascii=False),data,3,provenance={'method':'synthetic-not-real-AI'})


class EntityAssessmentTests(unittest.TestCase):
    def assess(self,data,response='仅有运营公司公开资料。',url=None,**changes):
        raw=answer('合成问题',response=response,displayed_sources=[{'url':url}] if url else [],citations=[],
            source_absence_verified=url is None,**changes)
        return assess_answer(data,raw,'合成问题')

    def test_three_objects_do_not_inherit_the_owner_or_sibling_product_mention(self):
        company=target(entity_type='company',name='北京同于野科技有限公司',company='',aliases=[],website='https://tongyuye.com/',official_urls=[])
        relay=target()
        desktop=target(name='同于野软件',website='https://tongyuye.com/products/tongyuye/',official_urls=[],aliases=['同于野桌面客户端'])
        response='北京同于野科技有限公司是一家科技公司。'
        expected=(True,False,False)
        for data,hit in zip((company,relay,desktop),expected):
            result=self.assess(data,response)
            self.assertEqual(result['brand_mentioned'],hit)
            self.assertTrue(result['company_mentioned'])
            self.assertEqual(result['related_company_mentioned'],None if data['entity_type']=='company' else True)
        for selected,other in ((relay,desktop),(desktop,relay),(company,desktop)):
            result=self.assess(selected,other['name']+'提供相关工具。')
            self.assertFalse(result['brand_mentioned'])
        self.assertTrue(self.assess(relay,'可查 DongAI 中转站，但资料不足。')['brand_mentioned'])
        self.assertTrue(self.assess(desktop,'同于野桌面客户端有公开介绍。')['brand_mentioned'])

    def test_new_target_terms_exclude_freeform_product_company_context_and_preserve_negative_mentions(self):
        for kind in ('product','company'):
            value=target(entity_type=kind)
            self.assertFalse(self.assess(value,'模型 API 服务支持接入。')['brand_mentioned'])
            self.assertFalse(self.assess(value,value['company']+'有公开资料。')['brand_mentioned'])
        negative=self.assess(target(),'未找到懂AI中转站的独立核验证据。')
        self.assertTrue(negative['brand_mentioned'])
        self.assertIsNone(negative['recommended'])
        self.assertIsNone(negative['fact_accuracy'])
        self.assertEqual(negative['evaluation_semantics'],'target_entity_scoped_urls')

    def test_owner_company_cannot_be_declared_a_product_alias_but_legacy_stays_unchanged(self):
        with self.assertRaisesRegex(ValueError,'公司全称'):
            normalize_profile(target(aliases=[' 北京 同于野科技有限公司 ']))
        legacy={**profile(),'aliases':['北京同于野科技有限公司']}
        self.assertTrue(self.assess(legacy,'北京同于野科技有限公司有公开说明。')['brand_mentioned'])

    def test_product_scopes_do_not_inherit_about_sibling_www_or_unlisted_subdomains(self):
        value=target()
        for url in ('https://tongyuye.com/','https://tongyuye.com/about/',
                    'https://tongyuye.com/products/tongyuye/','https://tongyuye.com/products/dongai-copy/',
                    'https://tongyuye.com/products/dongai2','https://www.tongyuye.com/products/dongai/',
                    'https://docs.tongyuye.com/products/dongai/','http://tongyuye.com/products/dongai/',
                    'https://tongyuye.com:444/products/dongai/','https://tongyuye.com.evil.org/products/dongai/',
                    'https://user@tongyuye.com/products/dongai/','https://evil.org/?next=https://tongyuye.com/products/dongai/'):
            with self.subTest(url=url):
                self.assertFalse(self.assess(value,url=url)['official_source_present'])
        for url in ('https://tongyuye.com/products/dongai','https://tongyuye.com:443/products/dongai/',
                    'https://tongyuye.com/products/dongai/docs?from=search#start','https://api.tongyuye.com/v1/'):
            with self.subTest(url=url):
                self.assertTrue(self.assess(value,url=url)['official_source_present'])
        company=target(entity_type='company',name='北京同于野科技有限公司',website='https://tongyuye.com/',official_urls=[],aliases=[])
        self.assertTrue(self.assess(company,url='https://tongyuye.com/about/')['official_source_present'])

    def test_unicode_percent_encoding_nfc_and_path_segment_boundaries(self):
        value=target(website='https://example.org/产品/懂AI/',official_urls=['https://example.org/café/'])
        for url in ('https://example.org/%E4%BA%A7%E5%93%81/%E6%87%82AI/',
                    'https://example.org/产品/懂AI/入门','https://example.org/cafe\u0301/api',
                    'https://example.org/caf%C3%A9/api'):
            self.assertTrue(self.assess(value,url=url)['official_source_present'],url)
        for url in ('https://example.org/产品/懂AI助手','https://example.org/产品%2F懂AI/',
                    'https://example.org/产品/懂AI/%2e%2e/other','https://example.org/产品/懂AI/%252e%252e/other',
                    'https://example.org/产品/懂AI/%FF','https://example.org/产品/懂AI/%5cother'):
            self.assertFalse(self.assess(value,url=url)['official_source_present'],url)
        self.assertEqual(canonical_entity_url('https://example.org/产品/懂AI/'),
            canonical_entity_url('https://example.org:443/%E4%BA%A7%E5%93%81/%E6%87%82AI'))
        self.assertEqual(canonical_entity_url('https://example.org/'),('https','example.org',443,'/'))

    def test_explicit_nondefault_port_is_valid_but_never_inherits_default_or_other_port(self):
        value=target(website='https://example.org:8443/product/',official_urls=[])
        self.assertEqual(normalize_profile(value)['website'],value['website'])
        self.assertEqual(canonical_entity_url(value['website']),('https','example.org',8443,'/product'))
        self.assertTrue(self.assess(value,url='https://example.org:8443/product/api')['official_source_present'])
        for url in ('https://example.org/product/','https://example.org:443/product/',
                    'https://example.org:9443/product/','http://example.org:8443/product/'):
            self.assertFalse(self.assess(value,url=url)['official_source_present'],url)

    def test_invalid_new_scope_configuration_is_rejected_without_changing_legacy_validation(self):
        invalid=(None,'https://example.org/',[None],['https://example.org/']*11,
            ['file:///tmp/x'],['https://user:pass@example.org/a'],['https://example.org/a?x=1'],
            ['https://example.org/a#part'],['https://example.org:0/a'],['https://example.org:65536/a'],
            ['https://example.org:/a'],['https://bad host.org/a'],
            ['https://example.org/a/%FF'],['https://example.org/a/%'],['https://example.org/a/%2F'],
            ['https://example.org/a/../b'],['https://example.org/a/%2e%2e/b'],['https://example.org/a/%252f'])
        for urls in invalid:
            with self.subTest(urls=urls),self.assertRaises(ValueError):normalize_profile(target(official_urls=urls))
        for website in ('https://example.org/a/../b','https://example.org/%FF'):
            with self.assertRaises(ValueError):normalize_profile(target(website=website))
            self.assertEqual(normalize_profile({**profile(),'website':website})['website'],website)
        with self.assertRaises(ValueError):normalize_profile({**profile(),'official_urls':[]})
        with self.assertRaises(ValueError):normalize_profile(target(entity_type='service'))

    def test_unknown_or_incomplete_answers_do_not_gain_related_company_or_official_metrics(self):
        for extra in ({'answer_completed':False},{'question_submitted':False},
                      {'response':'已委托专门调研代理，后台进行，完成后整理给你。'}):
            raw=answer('合成问题',response='北京同于野科技有限公司与懂AI中转站。',**{key:value for key,value in extra.items() if key!='response'})
            raw.update(extra)
            result=assess_answer(target(),raw,'合成问题')
            self.assertFalse(result['answer_evaluable'])
            self.assertIsNone(result['brand_mentioned'])
            self.assertIsNone(result['company_mentioned'])
            self.assertIsNone(result['related_company_mentioned'])
        raw=answer('合成问题',response='北京同于野科技有限公司。',collection_status='partial',displayed_sources=[],citations=[{'url':None}])
        result=assess_answer(target(),raw,'合成问题')
        self.assertFalse(result['brand_mentioned'])
        self.assertTrue(result['related_company_mentioned'])
        self.assertIsNone(result['official_source_present'])
        self.assertIsNone(result['official_citation_present'])


class EntityPlanAndReportTests(unittest.TestCase):
    def test_explicit_identity_question_must_name_target_while_discovery_forbids_owner_and_all_scoped_hosts(self):
        data=target();payload=questions(data)
        payload['cases'][1]['question']='北京同于野科技有限公司有哪些公开业务？'
        with self.assertRaisesRegex(ValueError,'点名'):parse_ai_plan(json.dumps(payload),data,3,provenance={})
        for term in ('北京同于野科技有限公司','懂AI中转站','DongAI 中转站','api.tongyuye.com','tongyuye.com'):
            payload=questions(data);payload['cases'][0]['question']='请比较 '+term+' 相关服务。'
            with self.assertRaisesRegex(ValueError,'泄漏'):parse_ai_plan(json.dumps(payload),data,3,provenance={})
        value=make_plan(data)
        self.assertEqual(validate_plan(value),value)
        mutated=copy.deepcopy(value);mutated['profile']['official_urls'].append('https://extra.example.org/')
        with self.assertRaisesRegex(ValueError,'变更'):validate_plan(mutated)

    def test_company_and_product_prompts_keep_categories_but_target_different_entities(self):
        for kind in ('company','product'):
            value=target(entity_type=kind)
            prompt=build_planning_prompt(value,3)
            self.assertIn('当前唯一评测对象是'+('公司' if kind=='company' else '该产品'),prompt)
            self.assertIn('"discovery": 1',prompt)
            self.assertIn('"brand_identity": 1',prompt)
            self.assertIn('"website_guided": 1',prompt)
        no_main=target(website='')
        self.assertIn('"website_guided": 0',build_planning_prompt(no_main,3))

    def test_legacy_plan_profile_hash_and_wide_metrics_are_preserved_and_new_reports_do_not_relabel_history(self):
        legacy=plan();before=copy.deepcopy(legacy)
        self.assertEqual(normalize_profile(legacy['profile']),legacy['profile'])
        self.assertNotIn('entity_type',legacy['profile'])
        self.assertNotIn('official_urls',legacy['profile'])
        self.assertEqual(validate_plan(legacy),before)
        self.assertEqual(plan_fingerprint(legacy),before['plan_sha256'])
        question=legacy['cases'][0]['question']
        old=assess_answer(legacy['profile'],answer(question,response='北京同于野科技有限公司。',
            citations=[{'url':'https://other.tongyuye.com/sibling'}]),question)
        self.assertTrue(old['brand_mentioned'])
        self.assertTrue(old['official_citation_present'])
        self.assertEqual(old['evaluation_semantics'],'legacy_mixed_entity_hostwide')
        new=make_plan(target())
        def report(value):
            return build_report(value,{'run_id':'synthetic-entity-'+str(len(value['cases'])),'status':'completed',
                'cases':{case['id']:{'status':'completed','result':answer(case['question'],response='北京同于野科技有限公司。',timestamp='2026-10-20T00:00:00Z')}
                         for case in value['cases']}})
        for value,semantics,hit in ((legacy,'legacy_mixed_entity_hostwide',True),(new,'target_entity_scoped_urls',False)):
            saved=report(value)
            self.assertEqual(saved['evaluation_semantics'],semantics)
            self.assertEqual(saved['cohorts']['discovery']['brand_mention_rate'],float(hit))
            original=json.dumps(saved,ensure_ascii=False).encode()
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json';path.write_bytes(original);mtime=path.stat().st_mtime_ns
                listed=list_saved_reports(Path(directory));read=read_saved_report(Path(directory),listed[0]['id'])
                self.assertEqual(listed[0]['evaluation_semantics'],semantics)
                self.assertEqual(read['evaluation_semantics'],semantics)
                self.assertEqual(read['cases'][0]['assessment']['brand_mentioned'],hit)
                self.assertEqual(path.read_bytes(),original)
                self.assertEqual(path.stat().st_mtime_ns,mtime)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),hashlib.sha256(original).digest())
