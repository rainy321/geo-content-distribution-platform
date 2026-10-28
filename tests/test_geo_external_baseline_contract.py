"""Pure fixtures: no database, browser, network, sampling or collected reports."""
import copy
import hashlib
import json
import re
import unittest
from unittest.mock import patch

from services.geo_external_baseline_contract import (
    prepare_plan, profile_for_project, profile_sha256, question_sha256, validate_request_id,
)
from services.geo_test_service import normalize_profile, plan_fingerprint, validate_plan


def profile(**changes):
    return normalize_profile({'name': '合成协作科技有限公司', 'entity_type': 'company',
        'company': '', 'industry': '团队协作', 'description': '面向企业的团队协作业务',
        'website': 'https://example.org/company/', 'aliases': ['合成协作'],
        'official_urls': ['https://public.example.org/about/'], **changes})


class ExternalBaselineContractTests(unittest.TestCase):
    def test_saved_project_json_lists_use_shared_profile_normalization(self):
        row = {**profile(), 'id': 17, 'created_at': 'not-profile-evidence',
               'keywords': '[" 团队 ","协作","团队"]', 'competitors': '[]',
               'aliases': '["合成协作"]', 'official_urls': '["https://public.example.org/about/"]'}
        before = copy.deepcopy(row)
        result = profile_for_project(row)
        self.assertEqual(result['keywords'], ['团队', '协作'])
        self.assertEqual(result['entity_type'], 'company')
        self.assertEqual(result['official_urls'], ['https://public.example.org/about/'])
        self.assertEqual(normalize_profile(result), result)
        self.assertNotIn('id', result); self.assertNotIn('created_at', result)
        self.assertEqual(row, before)

    def test_project_defaults_remain_explicit_target_entity_fields(self):
        result = profile_for_project({'name': '合成产品', 'industry': '团队协作'})
        self.assertEqual(result['entity_type'], 'product')
        self.assertEqual(result['company'], '')
        self.assertEqual(result['aliases'], [])
        self.assertEqual(result['official_urls'], [])
        self.assertEqual(result['product'], '合成产品')

    def test_malformed_or_non_string_project_lists_are_never_silently_dropped(self):
        for field in ('keywords', 'competitors', 'aliases', 'official_urls'):
            for value in ('{', '{}', 'null', '1', ['valid', 2], None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    profile_for_project({**profile(), field: value})
        for value in (None, [], 'project'):
            with self.assertRaises(ValueError): profile_for_project(value)

    def test_profile_fingerprint_is_canonical_and_covers_full_target_profile(self):
        original = profile()
        reordered = dict(reversed(list(original.items())))
        expected = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(profile_sha256(original), expected)
        self.assertEqual(profile_sha256(reordered), expected)
        for changes in ({'name': '另一合成公司'}, {'entity_type': 'product'},
                        {'website': 'https://example.org/other/'}, {'aliases': ['另一别名']},
                        {'description': '后来变更的业务说明'}, {'official_urls': []}):
            self.assertNotEqual(profile_sha256(profile(**changes)), expected)

    def test_template_is_deterministic_and_labeled_as_template_not_ai(self):
        original = profile(); before = copy.deepcopy(original)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
            first = prepare_plan(original); second = prepare_plan(original)
        self.assertEqual(first, second)
        self.assertEqual(first['profile'], original)
        self.assertEqual(first['profile_sha256'], profile_sha256(original))
        self.assertEqual(first['plan']['planner']['method'], 'template_not_ai')
        self.assertFalse(first['plan']['planner']['independent_verification'])
        self.assertIsNone(first['plan']['created_at'])
        self.assertTrue(first['requires_user_confirmation'])
        self.assertEqual(first['plan_sha256'], plan_fingerprint(first['plan']))
        self.assertEqual(validate_plan(first['plan']), first['plan'])
        self.assertEqual(original, before)

    def test_website_template_has_one_discovery_brand_and_exact_website_question(self):
        value = profile(); cases = prepare_plan(value)['plan']['cases']
        self.assertEqual([case['id'] for case in cases], ['q01', 'q02', 'q03'])
        self.assertEqual([case['category'] for case in cases], ['discovery', 'brand_identity', 'website_guided'])
        self.assertIn(value['name'], cases[1]['question'])
        self.assertIn(value['website'], cases[2]['question'])

    def test_no_website_requires_two_distinct_discovery_questions_and_no_guided_case(self):
        value = profile(website='', official_urls=[])
        cases = prepare_plan(value)['plan']['cases']
        self.assertEqual([case['category'] for case in cases], ['discovery', 'discovery', 'brand_identity'])
        self.assertNotEqual(cases[0]['question'], cases[1]['question'])
        self.assertTrue(all('http' not in case['question'] for case in cases))

    def test_template_does_not_leak_brand_company_alias_or_official_domain_via_industry(self):
        value = profile(entity_type='product', name='合成工具', company='合成运营有限公司', aliases=['合成别名'],
                        industry='合成工具 合成运营有限公司 合成别名 example.org')
        plan = prepare_plan(value)['plan']; self.assertEqual(validate_plan(plan), plan)
        fold = lambda text: re.sub(r'\s+', '', text).casefold()
        forbidden = [value['name'], value['company'], *value['aliases'], 'example.org', 'public.example.org']
        for case in plan['cases']:
            if case['category'] == 'discovery':
                for term in forbidden:
                    self.assertNotIn(fold(term), fold(case['question']))

    def test_broad_alias_collision_uses_another_template_without_deleting_identity(self):
        value = profile(name='公开', aliases=['方案'], website='', official_urls=[])
        plan = prepare_plan(value)['plan']
        self.assertEqual(validate_plan(plan), plan)
        self.assertEqual(plan['profile']['aliases'], ['方案'])
        for case in plan['cases']:
            if case['category'] == 'discovery':
                self.assertNotIn('公开', case['question']); self.assertNotIn('方案', case['question'])

    def test_impossible_discovery_template_collision_fails_closed(self):
        value = profile(aliases=['在', '选择', '有哪些', '采用', '怎样', '评估', '为新', '对照', '采购'])
        with self.assertRaisesRegex(ValueError, '模板问题'):
            prepare_plan(value)
        self.assertEqual(len(value['aliases']), 9)

    def test_existing_plan_keeps_exact_questions_and_digest_with_user_attributed_provenance(self):
        value = profile(); existing = prepare_plan(value)['plan']
        existing['cases'][0]['question'] += '\n请说明适用前提'
        existing['planner'] = {'method': 'claimed_ai_or_native_source'}
        existing['plan_sha256'] = plan_fingerprint(existing)
        before = copy.deepcopy(existing)
        result = prepare_plan(value, existing)
        self.assertEqual(result['plan']['cases'], existing['cases'])
        self.assertEqual(result['plan_sha256'], existing['plan_sha256'])
        self.assertEqual(result['plan']['planner']['method'], 'user_provided_not_verified')
        self.assertFalse(result['plan']['planner']['independent_verification'])
        self.assertEqual(result['plan']['profile_provenance'], 'client_supplied_not_verified')
        self.assertEqual(existing, before)

    def test_existing_plan_profile_mismatch_and_tampering_are_rejected(self):
        existing = prepare_plan(profile())['plan']
        with self.assertRaisesRegex(ValueError, '资料不一致'):
            prepare_plan(profile(name='另一家公司'), existing)
        changed = copy.deepcopy(existing); changed['cases'][0]['question'] += '不同问题'
        with self.assertRaises(ValueError): prepare_plan(profile(), changed)
        changed = copy.deepcopy(existing); changed['cases'][0]['question'] = '请推荐合成协作科技有限公司的方案'
        changed['plan_sha256'] = plan_fingerprint(changed)
        with self.assertRaisesRegex(ValueError, '泄漏'): prepare_plan(profile(), changed)
        for value in ([], 'plan', 1):
            with self.assertRaises(ValueError): prepare_plan(profile(), value)

    def test_prepare_does_not_freeze_or_infer_mobile_sampling_or_native_evidence(self):
        forbidden = {'new_conversation', 'first_message', 'answer_complete', 'answer_completed',
                     'question_submitted', 'native_verified', 'execution', 'sampling_conditions', 'frozen_at'}
        for prepared in (prepare_plan(profile()), prepare_plan(profile(), prepare_plan(profile())['plan'])):
            self.assertEqual(set(prepared), {'profile', 'profile_sha256', 'plan', 'plan_sha256', 'requires_user_confirmation'})
            self.assertFalse(forbidden.intersection(prepared))
            self.assertFalse(forbidden.intersection(prepared['plan']))
            self.assertNotIn('response', json.dumps(prepared))

    def test_prepare_returns_detached_profile_plan_and_input_snapshots(self):
        value = profile(); prepared = prepare_plan(value)
        prepared['profile']['aliases'].append('后来本地编辑')
        self.assertEqual(value['aliases'], ['合成协作'])
        self.assertEqual(prepared['plan']['profile']['aliases'], ['合成协作'])
        existing = prepare_plan(value)['plan']; result = prepare_plan(value, existing)
        result['plan']['cases'][0]['question'] = '只修改返回值'
        self.assertNotEqual(existing['cases'][0]['question'], '只修改返回值')

    def test_question_digest_preserves_utf8_text_whitespace_and_line_endings(self):
        original = '  原始问题\r\n第二行？ '
        self.assertEqual(question_sha256(original), hashlib.sha256(original.encode('utf-8')).hexdigest())
        self.assertNotEqual(question_sha256(original), question_sha256(original.strip()))
        self.assertNotEqual(question_sha256(original), question_sha256(original.replace('\r\n', '\n')))
        for value in (None, True, 1, [], '\ud800'):
            with self.assertRaises(ValueError): question_sha256(value)

    def test_request_identifier_is_strict_ascii_bounded_and_never_normalized(self):
        for value in ('a'*16, 'a'*100, '12345678-1234-5678-90ab-1234567890ab', 'ABCdef_0123456789-'):
            self.assertEqual(validate_request_id(value), value)
        for value in ('a'*15, 'a'*101, ' '+ 'a'*16, 'a'*16+'\n', 'a'*16+'/path', 'a'*16+'.',
                      '编号'*16, 'Ａ'*16, None, True, 16, []):
            with self.assertRaises(ValueError): validate_request_id(value)


if __name__ == '__main__':
    unittest.main()
